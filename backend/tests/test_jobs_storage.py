"""Real transactions and multiple connections exercise queue recovery and fencing."""

import json
import os
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_ingestion import MemorySource
from test_retrieval_storage import TestEmbeddings

from app.config import Settings
from app.db.schema import index_jobs, metadata, ready_indexes, snapshots
from app.db.session import make_engine
from app.ingestion.scanner import scan
from app.ingestion.service import save_snapshot
from app.jobs import worker
from app.jobs.service import LeaseLost, claim, enqueue, heartbeat, publish
from app.retrieval.embedding_store import profile


@pytest.fixture(name="job_engine")
def job_engine():
    url = os.environ.get("COPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set COPILOT_TEST_DATABASE_URL for PostgreSQL job/API tests")
    base = make_engine(Settings(_env_file=None, database_url=url))
    schema = "test_jobs_" + uuid4().hex
    with base.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = base.execution_options(schema_translate_map={None: schema})
    try:
        metadata.create_all(engine)
        yield engine
    finally:
        with base.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


@pytest.fixture(name="job_settings")
def job_settings():
    return Settings(_env_file=None, job_retry_seconds=1, job_lease_seconds=6, max_chunk_tokens=64)


@pytest.fixture(name="fake_ingestion")
def fake_ingestion(monkeypatch):
    state = {"calls": [], "sha": "a" * 40}

    def ingest(url, ref, settings, engine, *, on_resolved):
        state["calls"].append(ref)
        sha = ref if len(ref) == 40 else state["sha"]
        on_resolved(sha)
        source = MemorySource(
            [
                ("auth.py", "100644", b"def authenticate(token):\n    return token\n"),
                ("other.py", "100644", b"def other():\n    return None\n"),
            ]
        )
        with engine.begin() as conn:
            saved = save_snapshot(
                conn, url, sha, "test-jobs-v1", scan(source), time.monotonic() + 30
            )
        if state.get("crash"):
            state["crash"] = False
            raise KeyboardInterrupt
        return saved

    monkeypatch.setattr(worker, "ingest", ingest)
    return state


def submit(engine, settings, ref="main", url="https://github.com/example/repository"):
    with engine.begin() as conn:
        return enqueue(conn, url, ref, settings)


def get_job(engine, job):
    with engine.connect() as conn:
        return conn.execute(select(index_jobs).where(index_jobs.c.id == job["id"])).mappings().one()


def expire(engine, job):
    with engine.begin() as conn:
        conn.execute(
            index_jobs.update()
            .where(index_jobs.c.id == job["id"])
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )


def available(engine, job):
    with engine.begin() as conn:
        conn.execute(
            index_jobs.update()
            .where(index_jobs.c.id == job["id"])
            .values(available_at=func.now() - timedelta(seconds=1))
        )


def test_active_submission_deduplication_and_claim_skip_locked(job_engine, job_settings):
    first = submit(job_engine, job_settings)
    assert submit(job_engine, job_settings)["id"] == first["id"]
    second = submit(job_engine, job_settings, ref="next")
    with job_engine.begin() as locked:
        locked.execute(
            select(index_jobs.c.id).where(index_jobs.c.id == first["id"]).with_for_update()
        )
        claimed = claim(job_engine, job_settings)
        assert claimed["id"] == second["id"]
    assert claim(job_engine, job_settings)["id"] == first["id"]
    assert claim(job_engine, job_settings) is None


def test_expired_leases_are_reclaimed_and_old_owner_is_fenced(job_engine, job_settings):
    job = submit(job_engine, job_settings)
    old = claim(job_engine, job_settings)
    heartbeat(job_engine, old, job_settings, stage="ingesting", progress=5)
    expire(job_engine, job)
    new = claim(job_engine, job_settings)
    assert new["attempts"] == 2 and new["lease_token"] != old["lease_token"]
    with pytest.raises(LeaseLost):
        heartbeat(job_engine, old, job_settings)
    with pytest.raises(LeaseLost):
        publish(job_engine, old, uuid4(), uuid4(), "unknown")
    heartbeat(job_engine, new, job_settings)
    expire(job_engine, job)
    final = claim(job_engine, job_settings)
    assert final["attempts"] == 3
    expire(job_engine, job)
    assert claim(job_engine, job_settings) is None
    assert get_job(job_engine, job)["error"]["code"] == "lease_expired"


def test_worker_resume_preserves_commit_and_publishes_only_complete_index(
    job_engine, job_settings, fake_ingestion
):
    job = submit(job_engine, job_settings)
    provider = TestEmbeddings()
    fake_ingestion["crash"] = True
    with pytest.raises(KeyboardInterrupt):
        worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    assert get_job(job_engine, job)["resolved_commit_sha"] == "a" * 40
    expire(job_engine, job)
    fake_ingestion["sha"] = "b" * 40
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    saved = get_job(job_engine, job)
    assert saved["status"] == "succeeded" and saved["progress"] == 100
    assert fake_ingestion["calls"] == ["main", "a" * 40]
    with job_engine.connect() as conn:
        assert conn.execute(select(snapshots.c.commit_sha)).scalar_one() == "a" * 40
        assert conn.execute(select(snapshots.c.status)).scalar_one() == "ready"
        assert (
            conn.execute(select(ready_indexes.c.parsing_run_id)).scalar_one()
            == saved["parsing_run_id"]
        )
    calls = provider.calls
    repeat = submit(job_engine, job_settings, ref="a" * 40)
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    assert get_job(job_engine, repeat)["status"] == "succeeded"
    assert provider.calls == calls


def test_retry_reuses_completed_embedding_batches(job_engine, job_settings, fake_ingestion):
    settings = job_settings.model_copy(update={"embedding_batch_size": 1})
    job = submit(job_engine, settings)
    provider = TestEmbeddings()
    original = provider.embed
    attempts = []

    async def embed(texts, *, limits):
        attempts.extend(texts)
        if len(attempts) == 2:
            raise ValueError("secret-provider-message")
        return await original(texts, limits=limits)

    provider.embed = embed
    worker.run_once(job_engine, settings, embedding_factory=lambda _: provider)
    failed = get_job(job_engine, job)
    assert failed["status"] == "queued" and failed["stage"] == "embedding"
    assert "secret-provider-message" not in json.dumps(failed["error"])
    with job_engine.connect() as conn:
        assert conn.execute(select(ready_indexes)).first() is None
    available(job_engine, job)
    worker.run_once(job_engine, settings, embedding_factory=lambda _: provider)
    assert get_job(job_engine, job)["status"] == "succeeded"
    assert attempts.count(attempts[0]) == 1
    assert len(fake_ingestion["calls"]) == 1


def test_failed_replacement_keeps_old_ready_snapshot(
    job_engine, job_settings, fake_ingestion, monkeypatch
):
    provider = TestEmbeddings()
    first = submit(job_engine, job_settings)
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    old = get_job(job_engine, first)
    fake_ingestion["sha"] = "b" * 40
    new = submit(job_engine, job_settings.model_copy(update={"job_max_attempts": 1}))

    def fail_parse(*args):
        raise ValueError("bad source")

    monkeypatch.setattr(worker, "parse_snapshot", fail_parse)
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    assert get_job(job_engine, new)["status"] == "failed"
    with job_engine.connect() as conn:
        assert (
            conn.execute(
                select(snapshots.c.status).where(snapshots.c.id == old["snapshot_id"])
            ).scalar_one()
            == "ready"
        )
        assert conn.execute(select(ready_indexes.c.snapshot_id)).scalar_one() == old["snapshot_id"]


def test_live_repository_lock_defers_without_spending_attempt(
    job_engine, job_settings, fake_ingestion
):
    job = submit(job_engine, job_settings)
    with worker.repository_lock(job_engine, job["repository_id"]) as acquired:
        assert acquired
        worker.run_once(job_engine, job_settings, embedding_factory=lambda _: TestEmbeddings())
        saved = get_job(job_engine, job)
        assert saved["status"] == "queued" and saved["attempts"] == 0
        assert fake_ingestion["calls"] == []


def test_heartbeat_continues_during_slow_stage(job_engine, job_settings):
    job = submit(job_engine, job_settings)
    owned = claim(job_engine, job_settings)
    with worker.lease_guard(job_engine, owned, job_settings):
        before = get_job(job_engine, job)["heartbeat_at"]
        time.sleep(2.3)
        assert get_job(job_engine, job)["heartbeat_at"] > before


def test_cannot_publish_missing_or_wrong_snapshot_index(job_engine, job_settings, fake_ingestion):
    submit(job_engine, job_settings)
    owned = claim(job_engine, job_settings)
    with pytest.raises(ValueError, match="incomplete"):
        publish(job_engine, owned, uuid4(), uuid4(), profile(TestEmbeddings())["id"])


def test_configuration_has_no_secrets_and_changed_pipeline_requires_resubmission(
    job_engine, job_settings, monkeypatch
):
    job = submit(
        job_engine, job_settings.model_copy(update={"provider_api_key": "never-persist-this"})
    )
    assert "never-persist-this" not in json.dumps(job["configuration"])
    monkeypatch.setattr(worker, "pipeline_version", lambda _: "different")
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: TestEmbeddings())
    assert get_job(job_engine, job)["error"]["code"] == "pipeline_changed"
    assert get_job(job_engine, job)["status"] == "failed"
