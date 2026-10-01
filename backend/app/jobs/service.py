"""Short PostgreSQL transactions for queue ownership, retry, and publication."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.schema import (
    embedding_indexes,
    index_jobs,
    parsing_runs,
    ready_indexes,
    repositories,
    snapshots,
)
from app.ingestion.chunker import pipeline_version
from app.ingestion.clone import canonical_url, validate_ref
from app.ingestion.scanner import index_version

CONFIGURATION_FIELDS = (
    "clone_timeout_seconds",
    "ingestion_timeout_seconds",
    "max_clone_bytes",
    "max_file_bytes",
    "max_source_bytes",
    "max_repository_files",
    "max_chunk_tokens",
    "max_snapshot_chunks",
    "parsing_timeout_seconds",
    "embedding_model_id",
    "embedding_dimensions",
    "embedding_model_version",
    "embedding_batch_size",
    "embedding_token_budget",
    "embedding_timeout_seconds",
)


class LeaseLost(RuntimeError):
    pass


def enqueue(connection, url, ref, settings):
    url, ref = canonical_url(url), validate_ref(ref)
    configuration = settings.model_dump(mode="json", include=set(CONFIGURATION_FIELDS))
    configuration["pipeline_version"] = pipeline_version(settings.max_chunk_tokens)
    configuration["source_version"] = index_version(settings)
    key = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    connection.execute(
        insert(repositories)
        .values(id=uuid4(), canonical_url=url, created_at=func.clock_timestamp())
        .on_conflict_do_nothing(index_elements=["canonical_url"])
    )
    repository_id = connection.execute(
        select(repositories.c.id).where(repositories.c.canonical_url == url)
    ).scalar_one()
    # Serialize enqueue for one repository; active-job uniqueness also protects other clients.
    connection.execute(
        select(repositories.c.id).where(repositories.c.id == repository_id).with_for_update()
    )
    existing = (
        connection.execute(
            select(index_jobs).where(
                index_jobs.c.repository_id == repository_id,
                index_jobs.c.requested_ref == ref,
                index_jobs.c.configuration_hash == key,
                index_jobs.c.status.in_(["queued", "running"]),
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        return dict(existing)
    return dict(
        connection.execute(
            index_jobs.insert()
            .values(
                id=uuid4(),
                repository_id=repository_id,
                requested_ref=ref,
                configuration=configuration,
                configuration_hash=key,
                status="queued",
                stage="queued",
                progress=0,
                attempts=0,
                max_attempts=settings.job_max_attempts,
                available_at=func.clock_timestamp(),
                created_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
            .returning(index_jobs)
        )
        .mappings()
        .one()
    )


def claim(engine, settings):
    with engine.begin() as connection:
        # Expired final attempts become terminal even if their process never reported failure.
        expired = (
            connection.execute(
                select(index_jobs.c.id)
                .where(
                    index_jobs.c.status == "running",
                    index_jobs.c.lease_expires_at <= func.clock_timestamp(),
                    index_jobs.c.attempts >= index_jobs.c.max_attempts,
                )
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        if expired:
            connection.execute(
                index_jobs.update()
                .where(index_jobs.c.id.in_(expired))
                .values(
                    status="failed",
                    lease_token=None,
                    lease_expires_at=None,
                    finished_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                    error={
                        "code": "lease_expired",
                        "message": "Worker lease expired after the final attempt.",
                    },
                )
            )
        job = (
            connection.execute(
                select(index_jobs)
                .where(
                    index_jobs.c.attempts < index_jobs.c.max_attempts,
                    or_(
                        and_(
                            index_jobs.c.status == "queued",
                            index_jobs.c.available_at <= func.clock_timestamp(),
                        ),
                        and_(
                            index_jobs.c.status == "running",
                            index_jobs.c.lease_expires_at <= func.clock_timestamp(),
                        ),
                    ),
                )
                .order_by(index_jobs.c.created_at, index_jobs.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if job is None:
            return None
        return dict(
            connection.execute(
                index_jobs.update()
                .where(index_jobs.c.id == job["id"])
                .values(
                    status="running",
                    attempts=job["attempts"] + 1,
                    lease_token=uuid4(),
                    lease_expires_at=func.clock_timestamp()
                    + timedelta(seconds=settings.job_lease_seconds),
                    heartbeat_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                    error=None,
                )
                .returning(index_jobs)
            )
            .mappings()
            .one()
        )


def owned(job):
    return and_(
        index_jobs.c.id == job["id"],
        index_jobs.c.status == "running",
        index_jobs.c.lease_token == job["lease_token"],
        index_jobs.c.lease_expires_at > func.clock_timestamp(),
    )


def heartbeat(engine, job, settings, **values):
    with engine.begin() as connection:
        result = connection.execute(
            index_jobs.update()
            .where(owned(job))
            .values(
                lease_expires_at=func.clock_timestamp()
                + timedelta(seconds=settings.job_lease_seconds),
                heartbeat_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
                **values,
            )
        )
        if result.rowcount != 1:
            raise LeaseLost("Indexing job lease is no longer owned by this worker")


def fail(engine, job, settings, code, *, retry=True):
    with engine.begin() as connection:
        terminal = not retry or job["attempts"] >= job["max_attempts"]
        return (
            connection.execute(
                index_jobs.update()
                .where(owned(job))
                .values(
                    status="failed" if terminal else "queued",
                    lease_token=None,
                    lease_expires_at=None,
                    available_at=func.clock_timestamp()
                    + timedelta(seconds=settings.job_retry_seconds * 2 ** (job["attempts"] - 1)),
                    updated_at=func.clock_timestamp(),
                    finished_at=func.clock_timestamp() if terminal else None,
                    error={
                        "code": code,
                        "message": "Indexing failed; inspect the stage and retry configuration.",
                    },
                )
            ).rowcount
            == 1
        )


def defer_busy(engine, job, settings):
    with engine.begin() as connection:
        connection.execute(
            index_jobs.update()
            .where(owned(job))
            .values(
                status="queued",
                attempts=job["attempts"] - 1,
                lease_token=None,
                lease_expires_at=None,
                available_at=func.clock_timestamp() + timedelta(seconds=settings.job_retry_seconds),
                updated_at=func.clock_timestamp(),
            )
        )


def publish(engine, job, snapshot_id, run_id, profile_id):
    with engine.begin() as connection:
        found = connection.execute(
            select(index_jobs.c.id).where(owned(job)).with_for_update()
        ).scalar_one_or_none()
        if found is None:
            raise LeaseLost("Cannot publish with an expired worker lease")
        ready = connection.execute(
            select(embedding_indexes.c.chunk_count)
            .join(
                parsing_runs,
                parsing_runs.c.id == embedding_indexes.c.parsing_run_id,
            )
            .join(snapshots, snapshots.c.id == parsing_runs.c.snapshot_id)
            .where(
                parsing_runs.c.snapshot_id == snapshot_id,
                parsing_runs.c.id == run_id,
                snapshots.c.repository_id == job["repository_id"],
                embedding_indexes.c.profile_id == profile_id,
            )
        ).scalar_one_or_none()
        if ready is None:
            raise ValueError("Cannot publish an incomplete or mismatched index")
        connection.execute(
            insert(ready_indexes)
            .values(
                snapshot_id=snapshot_id,
                parsing_run_id=run_id,
                profile_id=profile_id,
                created_at=func.clock_timestamp(),
            )
            .on_conflict_do_nothing()
        )
        connection.execute(
            snapshots.update().where(snapshots.c.id == snapshot_id).values(status="ready")
        )
        connection.execute(
            index_jobs.update()
            .where(index_jobs.c.id == job["id"])
            .values(
                snapshot_id=snapshot_id,
                parsing_run_id=run_id,
                status="succeeded",
                stage="complete",
                progress=100,
                lease_token=None,
                lease_expires_at=None,
                updated_at=func.clock_timestamp(),
                finished_at=func.clock_timestamp(),
                error=None,
            )
        )


def ready_index(connection, snapshot_id, profile_id=None):
    query = (
        select(ready_indexes)
        .join(snapshots, snapshots.c.id == ready_indexes.c.snapshot_id)
        .where(
            ready_indexes.c.snapshot_id == snapshot_id,
            snapshots.c.status == "ready",
        )
    )
    if profile_id is not None:
        query = query.where(ready_indexes.c.profile_id == profile_id)
    return (
        connection.execute(
            query.order_by(ready_indexes.c.created_at.desc(), ready_indexes.c.parsing_run_id).limit(
                1
            )
        )
        .mappings()
        .one_or_none()
    )


def public_job(job):
    return {
        key: value
        for key, value in job.items()
        if key not in {"configuration", "configuration_hash", "lease_token"}
    }
