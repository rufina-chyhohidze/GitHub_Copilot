"""Independent answer worker; disconnects never own execution or retry model calls."""

import argparse
import asyncio
import json
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.answering.pipeline import answer
from app.config import Settings
from app.conversations.service import RunStopped, claim, emit, finish, get, owned, terminate
from app.db.schema import answer_runs, conversations, messages
from app.db.session import make_engine
from app.providers.embeddings import OpenAIEmbeddings
from app.providers.text import OpenAITextModel
from app.retrieval.embedding_store import profile


def context(connection, run, budget=4000):
    # Only completed turns contribute context. They remain untrusted, not source evidence.
    rows = (
        connection.execute(
            select(answer_runs.c.input_message_id, answer_runs.c.output_message_id)
            .where(
                answer_runs.c.conversation_id == run["conversation_id"],
                answer_runs.c.status == "completed",
            )
            .order_by(answer_runs.c.created_at.desc())
            .limit(8)
        )
        .mappings()
        .all()
    )
    history = []
    for row in rows:
        pair = []
        for key in ("input_message_id", "output_message_id"):
            message = get(connection, messages, row[key])
            content = message["content"]
            pair.append({"role": message["role"], "content": content.get("answer", content)})
        size = len(json.dumps(pair).encode())
        if size > budget:
            break
        budget -= size
        history[0:0] = pair
    return history


async def execute(engine, settings, run, provider, model, planner):
    with engine.connect() as connection:
        conversation = get(connection, conversations, run["conversation_id"])
        question = get(connection, messages, run["input_message_id"])["content"]["text"]
        history = context(connection, run, min(4000, settings.max_context_tokens // 4))
    if profile(provider)["id"] != conversation["profile_id"]:
        raise ValueError("The pinned embedding profile is not configured")

    def notify(kind, payload):
        emit(engine, run, kind, payload)

    async def generate():
        result = await answer(
            engine,
            conversation["snapshot_id"],
            question,
            settings,
            model,
            provider=provider,
            run_id=conversation["parsing_run_id"],
            pipeline=run["pipeline"],
            planner=planner,
            answer_run_id=run["id"],
            on_event=notify,
            history=history,
        )
        if result["status"] == "completed":
            prose = "\n\n".join(
                [claim["text"] for claim in result["answer"]["claims"]]
                + ([result["answer"]["uncertainty"]] if result["answer"]["uncertainty"] else [])
            )
            for start in range(0, len(prose), 512):
                notify("answer_delta", {"text": prose[start : start + 512], "provisional": True})
        finish(engine, run, result)

    task = asyncio.create_task(generate())
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            with engine.begin() as connection:
                owned(connection, run["id"], run["lease_token"])
        await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def run_once(
    engine,
    settings,
    *,
    embedding_factory=OpenAIEmbeddings,
    text_factory=OpenAITextModel,
    planner_factory=None,
):
    run = claim(engine, settings)
    if run is None:
        return None
    try:
        asyncio.run(
            execute(
                engine,
                settings,
                run,
                embedding_factory(settings),
                text_factory(settings),
                planner_factory(settings)
                if planner_factory and run["pipeline"] == "agent"
                else None,
            )
        )
    except RunStopped:
        pass
    except Exception:
        # No provider text, source content, or credentials in public errors.
        try:
            with engine.begin() as connection:
                current = owned(connection, run["id"], run["lease_token"])
                terminate(connection, current, "failed", "answer_failed")
        except RunStopped:
            pass
    return run["id"]


def main():
    parser = argparse.ArgumentParser(prog="repo-copilot-answer-worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        settings = Settings()
        engine = make_engine(settings)
        while True:
            identity = run_once(engine, settings)
            if identity:
                print(
                    json.dumps({"event": "answer_run_processed", "run_id": str(identity)}),
                    flush=True,
                )
            if args.once:
                return 0
            if identity is None:
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        return 0
    except (ValueError, SQLAlchemyError, OSError):
        print(json.dumps({"error": "Answer worker unavailable; check configuration and database."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
