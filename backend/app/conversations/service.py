"""Transactions serialize submissions and events; terminal states fence stale workers."""

from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.schema import answer_runs, conversations, messages, run_events, run_evidence

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class RunStopped(Exception):
    """The worker no longer owns a live run."""


def get(connection, table, identity, *, lock=False):
    query = select(table).where(table.c.id == identity)
    if lock:
        query = query.with_for_update()
    row = connection.execute(query).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Conversation or run not found.")
    return dict(row)


def event(connection, run, kind, payload):
    # Caller holds the run row lock (or has just inserted it).
    run["sequence"] += 1
    connection.execute(
        run_events.insert().values(
            run_id=run["id"],
            sequence=run["sequence"],
            type=kind,
            payload=payload,
            created_at=func.clock_timestamp(),
        )
    )
    connection.execute(
        answer_runs.update()
        .where(answer_runs.c.id == run["id"])
        .values(
            sequence=run["sequence"],
        )
    )


def terminate(connection, run, status, code=None):
    if run["status"] in TERMINAL:
        return
    connection.execute(
        answer_runs.update()
        .where(answer_runs.c.id == run["id"])
        .values(
            status=status,
            error=code,
            finished_at=func.now(),
            lease_token=None,
        )
    )
    if code:
        event(connection, run, "error", {"code": code})
    event(connection, run, "done", {"status": status, "validated": False})
    run["status"] = status


def recover(connection):
    expired = (
        connection.execute(
            select(answer_runs)
            .where(
                answer_runs.c.status == "running",
                answer_runs.c.expires_at <= func.now(),
            )
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .all()
    )
    for run in expired:
        terminate(connection, dict(run), "interrupted", "worker_interrupted")


def submit(connection, conversation_id, question, pipeline, key):
    get(connection, conversations, conversation_id, lock=True)
    existing = (
        connection.execute(
            select(answer_runs).where(
                answer_runs.c.conversation_id == conversation_id,
                answer_runs.c.idempotency_key == key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        original = get(connection, messages, existing["input_message_id"])
        if original["content"] != {"text": question} or existing["pipeline"] != pipeline:
            raise HTTPException(409, "Idempotency key already used for a different request.")
        return dict(existing)
    active = connection.execute(
        select(answer_runs.c.id).where(
            answer_runs.c.conversation_id == conversation_id,
            answer_runs.c.status.in_(["queued", "running"]),
        )
    ).first()
    if active:
        raise HTTPException(409, "This conversation already has an active answer run.")
    mid, rid = uuid4(), uuid4()
    connection.execute(
        messages.insert().values(
            id=mid,
            conversation_id=conversation_id,
            role="user",
            content={"text": question},
            created_at=func.clock_timestamp(),
        )
    )
    connection.execute(
        answer_runs.insert().values(
            id=rid,
            conversation_id=conversation_id,
            input_message_id=mid,
            idempotency_key=key,
            pipeline=pipeline,
            status="queued",
            sequence=0,
            created_at=func.clock_timestamp(),
        )
    )
    run = get(connection, answer_runs, rid)
    event(connection, run, "status", {"status": "queued"})
    return run


def claim(engine, settings):
    with engine.begin() as connection:
        recover(connection)
        row = (
            connection.execute(
                select(answer_runs)
                .where(answer_runs.c.status == "queued")
                .order_by(answer_runs.c.created_at, answer_runs.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        run = dict(row)
        token = uuid4()
        limits = {
            name: getattr(settings, name)
            for name in (
                "run_timeout_seconds",
                "max_tool_calls",
                "max_context_tokens",
                "max_output_tokens",
                "agent_max_model_calls",
                "agent_input_token_budget",
                "agent_output_token_budget",
                "agent_tool_result_bytes",
                "agent_total_tool_bytes",
            )
        }
        connection.execute(
            answer_runs.update()
            .where(answer_runs.c.id == run["id"])
            .values(
                status="running",
                lease_token=token,
                expires_at=func.now() + timedelta(seconds=settings.run_timeout_seconds + 15),
                model_id=settings.model_id,
                limits=limits,
            )
        )
        event(connection, run, "status", {"status": "running"})
        return get(connection, answer_runs, run["id"])


def owned(connection, identity, token):
    run = get(connection, answer_runs, identity, lock=True)
    now = connection.execute(select(func.clock_timestamp())).scalar_one()
    if run["status"] != "running" or run["lease_token"] != token or run["expires_at"] <= now:
        raise RunStopped
    return run


def emit(engine, run, kind, payload):
    with engine.begin() as connection:
        current = owned(connection, run["id"], run["lease_token"])
        event(connection, current, kind, payload)


def finish(engine, run, result):
    with engine.begin() as connection:
        current = owned(connection, run["id"], run["lease_token"])
        connection.execute(
            answer_runs.update()
            .where(answer_runs.c.id == run["id"])
            .values(
                usage=result.get("usage"),
            )
        )
        if result["status"] != "completed":
            terminate(connection, current, "failed", "answer_failed")
            return
        # Pipeline validates exact source hashes and spans before this atomic publication.
        for item in result["citations"]:
            if item["run_id"] != str(run["id"]):
                raise ValueError("Mismatched answer run")
            span = item["source"]
            connection.execute(
                run_evidence.insert().values(
                    id=UUID(item["id"]),
                    run_id=run["id"],
                    file_id=UUID(span["file_id"]),
                    start_line=span["start_line"],
                    end_line=span["end_line"],
                    content_hash=item["content_hash"],
                    originating_tool=item["originating_tool"],
                    citation=item,
                )
            )
            event(connection, current, "citation", {"citation": item, "validated": True})
        mid = uuid4()
        content = {key: result[key] for key in ("answer", "citations", "snapshot_id", "commit_sha")}
        content.update(run_id=str(run["id"]), validated=True)
        connection.execute(
            messages.insert().values(
                id=mid,
                conversation_id=run["conversation_id"],
                role="assistant",
                content=content,
                created_at=func.clock_timestamp(),
            )
        )
        connection.execute(
            answer_runs.update()
            .where(answer_runs.c.id == run["id"])
            .values(
                status="completed",
                output_message_id=mid,
                usage=result["usage"],
                finished_at=func.now(),
                lease_token=None,
            )
        )
        event(
            connection,
            current,
            "done",
            {
                "status": "completed",
                "validated": True,
                "message_id": str(mid),
                **content,
            },
        )


def public_run(run):
    return {
        key: run[key]
        for key in (
            "id",
            "conversation_id",
            "input_message_id",
            "output_message_id",
            "pipeline",
            "status",
            "model_id",
            "limits",
            "usage",
            "error",
            "sequence",
            "created_at",
            "finished_at",
        )
    }
