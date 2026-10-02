"""HTTP submission is separate from run execution and replayable SSE observation."""

import asyncio
import json
from uuid import UUID, uuid4

from fastapi import Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.conversations.service import (
    TERMINAL,
    get,
    public_run,
    recover,
    submit,
    terminate,
)
from app.db.schema import answer_runs, conversations, messages, run_events
from app.models.contracts import Contract


class ConversationRequest(Contract):
    title: str = Field(default="Repository conversation", min_length=1, max_length=200)


def frame(row):
    return (
        f"id: {row['sequence']}\nevent: {row['type']}\n"
        f"data: {json.dumps(row['payload'], ensure_ascii=False)}\n\n"
    )


def install(app, require_ready, question_type):
    @app.post("/snapshots/{snapshot_id}/conversations", status_code=201)
    def create_conversation(snapshot_id: UUID, body: ConversationRequest):
        with app.state.engine.begin() as connection:
            published = require_ready(connection, snapshot_id)
            identity = uuid4()
            connection.execute(
                conversations.insert().values(
                    id=identity,
                    snapshot_id=snapshot_id,
                    parsing_run_id=published["parsing_run_id"],
                    profile_id=published["profile_id"],
                    title=body.title,
                    created_at=func.now(),
                )
            )
            return get(connection, conversations, identity)

    @app.get("/snapshots/{snapshot_id}/conversations")
    def list_conversations(
        snapshot_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        with app.state.engine.connect() as connection:
            require_ready(connection, snapshot_id)
            rows = (
                connection.execute(
                    select(conversations)
                    .where(conversations.c.snapshot_id == snapshot_id)
                    .order_by(conversations.c.created_at, conversations.c.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
            return {
                "items": rows[:limit],
                "next_offset": offset + limit if len(rows) > limit else None,
            }

    @app.get("/conversations/{conversation_id}")
    def conversation(
        conversation_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        with app.state.engine.begin() as connection:
            recover(connection)
            result = get(connection, conversations, conversation_id)
            rows = (
                connection.execute(
                    select(messages)
                    .where(messages.c.conversation_id == conversation_id)
                    .order_by(messages.c.created_at, messages.c.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
            runs = (
                connection.execute(
                    select(answer_runs).where(
                        answer_runs.c.input_message_id.in_([row["id"] for row in rows[:limit]])
                    )
                )
                .mappings()
                .all()
            )
            result.update(
                messages=rows[:limit],
                runs=[public_run(run) for run in runs],
                next_offset=offset + limit if len(rows) > limit else None,
            )
            return result

    @app.post("/conversations/{conversation_id}/messages", status_code=202)
    def message(
        conversation_id: UUID,
        body: question_type,
        idempotency_key: str = Header(min_length=1, max_length=128, pattern=r"^[!-~]+$"),
    ):
        with app.state.engine.begin() as connection:
            recover(connection)
            run = submit(connection, conversation_id, body.question, body.pipeline, idempotency_key)
            return {
                "run_id": run["id"],
                "status": run["status"],
                "events_url": f"/runs/{run['id']}/events",
            }

    @app.get("/runs/{run_id}")
    def run_status(run_id: UUID):
        with app.state.engine.begin() as connection:
            recover(connection)
            return public_run(get(connection, answer_runs, run_id))

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: UUID):
        with app.state.engine.begin() as connection:
            run = get(connection, answer_runs, run_id, lock=True)
            terminate(connection, run, "cancelled")
            return public_run(get(connection, answer_runs, run_id))

    @app.get("/runs/{run_id}/events")
    def events(
        run_id: UUID,
        after: int = Query(0, ge=0),
        last_event_id: int | None = Header(default=None, ge=0),
    ):
        cursor = last_event_id if last_event_id is not None else after
        with app.state.engine.begin() as connection:
            recover(connection)
            run = get(connection, answer_runs, run_id)
            if cursor > run["sequence"]:
                raise HTTPException(400, "Event cursor exceeds the run's last event.")
        if cursor == run["sequence"] and run["status"] in TERMINAL:
            # EventSource stops reconnecting on 204 when it has already seen done.
            from fastapi import Response

            return Response(status_code=204)

        def page(sequence):
            with app.state.engine.begin() as connection:
                recover(connection)
                rows = (
                    connection.execute(
                        select(run_events)
                        .where(
                            run_events.c.run_id == run_id,
                            run_events.c.sequence > sequence,
                        )
                        .order_by(run_events.c.sequence)
                        .limit(100)
                    )
                    .mappings()
                    .all()
                )
                current = get(connection, answer_runs, run_id)
                return rows, current

        async def stream():
            sequence = cursor
            idle = 0
            while True:
                rows, current = await run_in_threadpool(page, sequence)
                for row in rows:
                    sequence = row["sequence"]
                    yield frame(row)
                if current["status"] in TERMINAL and sequence >= current["sequence"]:
                    return
                if not rows:
                    idle += 1
                    if idle % 30 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
