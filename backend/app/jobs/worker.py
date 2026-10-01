"""Independent worker: bounded stages, renewable leases, and fenced publication."""

import argparse
import asyncio
import json
import threading
import time
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.schema import repositories
from app.db.session import make_engine
from app.ingestion.chunker import pipeline_version
from app.ingestion.parsing import parse_snapshot
from app.ingestion.scanner import index_version
from app.ingestion.service import ingest
from app.jobs.service import LeaseLost, claim, defer_busy, fail, heartbeat, publish
from app.providers.embeddings import OpenAIEmbeddings
from app.retrieval.embedding_store import build_index


@contextmanager
def repository_lock(engine, repository_id):
    # A session lock outlives stage transactions and is released on process/connection death.
    key = int.from_bytes(repository_id.bytes[:8], "big", signed=True)
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        ).scalar_one()
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    connection.commit()
                except SQLAlchemyError:
                    connection.invalidate()  # Never return a possibly locked session to the pool.


@contextmanager
def lease_guard(engine, job, settings):
    stopped = threading.Event()
    lost = threading.Event()

    def renew():
        while not stopped.wait(settings.job_lease_seconds / 3):
            try:
                heartbeat(engine, job, settings)
            except (LeaseLost, SQLAlchemyError):
                lost.set()
                return

    thread = threading.Thread(target=renew, name="index-lease", daemon=True)
    thread.start()

    def checkpoint(**values):
        if lost.is_set():
            raise LeaseLost("Worker heartbeat failed")
        heartbeat(engine, job, settings, **values)
        job.update(values)

    try:
        yield checkpoint
    finally:
        stopped.set()
        thread.join()


def run_once(engine, settings, *, embedding_factory=OpenAIEmbeddings):
    job = claim(engine, settings)
    if job is None:
        return None
    stage = "configuration"
    try:
        with repository_lock(engine, job["repository_id"]) as acquired:
            if not acquired:
                defer_busy(engine, job, settings)
                return job["id"]
            with lease_guard(engine, job, settings) as checkpoint:
                config = dict(job["configuration"])
                expected_pipeline = config.pop("pipeline_version")
                expected_source = config.pop("source_version")
                task_settings = Settings(
                    _env_file=None, **config, provider_api_key=settings.provider_api_key
                )
                if (
                    pipeline_version(task_settings.max_chunk_tokens) != expected_pipeline
                    or index_version(task_settings) != expected_source
                ):
                    fail(engine, job, settings, "pipeline_changed", retry=False)
                    return job["id"]
                provider = embedding_factory(task_settings)
                with engine.connect() as connection:
                    url = connection.execute(
                        select(repositories.c.canonical_url).where(
                            repositories.c.id == job["repository_id"]
                        )
                    ).scalar_one()
                if job["snapshot_id"] is None:
                    stage = "ingesting"
                    checkpoint(stage=stage, progress=5)
                    result = ingest(
                        url,
                        job["resolved_commit_sha"] or job["requested_ref"],
                        task_settings,
                        engine,
                        on_resolved=lambda sha: checkpoint(resolved_commit_sha=sha),
                    )
                    checkpoint(snapshot_id=UUID(result["snapshot_id"]), progress=35)
                stage = "parsing"
                checkpoint(stage=stage, progress=40)
                parsed = parse_snapshot(job["snapshot_id"], task_settings, engine)
                checkpoint(parsing_run_id=UUID(parsed["parsing_run_id"]), progress=60)
                stage = "embedding"
                checkpoint(stage=stage, progress=65)
                indexed = asyncio.run(
                    build_index(
                        engine, job["snapshot_id"], task_settings, provider, job["parsing_run_id"]
                    )
                )
                stage = "publishing"
                checkpoint(stage=stage, progress=95)
                publish(
                    engine, job, job["snapshot_id"], job["parsing_run_id"], indexed["profile_id"]
                )
    except LeaseLost:
        pass  # A new owner controls retry and terminal state; stale workers cannot write either.
    except (ValueError, SQLAlchemyError, OSError):
        fail(engine, job, settings, f"{stage}_failed", retry=stage != "configuration")
    except Exception:
        # Persist a safe error without source/provider/credential text; keep the worker alive.
        fail(engine, job, settings, "internal_error")
    return job["id"]


def main():
    parser = argparse.ArgumentParser(prog="repo-copilot-worker")
    parser.add_argument("--once", action="store_true", help="Attempt one available job and exit")
    args = parser.parse_args()
    engine = None
    try:
        settings = Settings()
        engine = make_engine(settings)
        while True:
            job_id = run_once(engine, settings)
            if job_id is not None:
                print(
                    json.dumps({"event": "index_job_processed", "job_id": str(job_id)}), flush=True
                )
            if args.once:
                return 0
            if job_id is None:
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        return 0  # Unfinished leases expire and another process can resume them.
    except (ValueError, SQLAlchemyError, OSError):
        print(json.dumps({"error": "Worker unavailable; check database and worker configuration."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
