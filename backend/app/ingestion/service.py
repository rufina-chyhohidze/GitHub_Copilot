"""Publish complete ingestion results atomically; never overwrite stored source."""

import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from app.config import Settings
from app.db.schema import repositories, repository_files, snapshots
from app.ingestion.clone import IngestionError, acquire, canonical_url, validate_ref
from app.ingestion.scanner import ScanResult, index_version, manifest_json, scan


def save_snapshot(
    connection: Connection,
    url: str,
    commit_sha: str,
    version: str,
    result: ScanResult,
    deadline: float,
) -> dict:
    """The caller owns the transaction so any failure rolls back all newly stored rows."""

    def check_time() -> None:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise IngestionError("Repository ingestion exceeded its time limit")
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": str(remaining_ms)},
        )

    check_time()
    now = datetime.now(UTC)
    connection.execute(
        insert(repositories)
        .values(id=uuid4(), canonical_url=url, created_at=now)
        .on_conflict_do_nothing(index_elements=["canonical_url"])
    )
    repository_id = connection.execute(
        select(repositories.c.id).where(repositories.c.canonical_url == url)
    ).scalar_one()
    snapshot_id = uuid4()
    check_time()
    created = connection.execute(
        insert(snapshots)
        .values(
            id=snapshot_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            index_version=version,
            status="ingested",
            coverage=result.coverage(),
            manifest=manifest_json(result),
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["repository_id", "commit_sha", "index_version"])
        .returning(snapshots.c.id)
    ).scalar_one_or_none()
    if created is not None:
        for offset in range(0, len(result.files), 100):
            check_time()
            connection.execute(
                repository_files.insert(),
                [
                    {"id": uuid4(), "snapshot_id": snapshot_id, **asdict(file)}
                    for file in result.files[offset : offset + 100]
                ],
            )
    check_time()
    stored = (
        connection.execute(
            select(snapshots).where(
                snapshots.c.repository_id == repository_id,
                snapshots.c.commit_sha == commit_sha,
                snapshots.c.index_version == version,
            )
        )
        .mappings()
        .one()
    )
    return {
        "repository_id": str(repository_id),
        "snapshot_id": str(stored["id"]),
        "canonical_url": url,
        "commit_sha": stored["commit_sha"],
        "index_version": stored["index_version"],
        "status": stored["status"],
        "coverage": stored["coverage"],
        "reused": created is None,
    }


def ingest(url: str, ref: str, settings: Settings, engine: Engine) -> dict:
    url, ref = canonical_url(url), validate_ref(ref)
    with acquire(url, ref, settings) as source:
        result = scan(source)
        source.check_deadline()
        with engine.begin() as connection:
            response = save_snapshot(
                connection, url, source.commit_sha, index_version(settings), result, source.deadline
            )
            source.check_deadline()
        return response
