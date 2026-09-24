"""Opt-in PostgreSQL tests use an isolated schema rolled back after each test."""

import hashlib
import os
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.schema import (
    code_chunks,
    metadata,
    parsing_runs,
    repositories,
    repository_files,
    snapshots,
)
from app.db.session import make_engine
from app.ingestion.clone import IngestionError
from app.ingestion.parsing import parse_and_store
from app.ingestion.scanner import ManifestEntry, ScanResult, StoredFile
from app.ingestion.service import save_snapshot

pytestmark = pytest.mark.skipif(
    not os.environ.get("COPILOT_TEST_DATABASE_URL"),
    reason="Set COPILOT_TEST_DATABASE_URL to run isolated PostgreSQL storage tests",
)


@pytest.fixture
def connection():
    settings = Settings(_env_file=None, database_url=os.environ["COPILOT_TEST_DATABASE_URL"])
    engine = make_engine(settings)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = "test_ingestion_" + uuid4().hex
        try:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection = connection.execution_options(schema_translate_map={None: schema})
            metadata.create_all(connection)
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def result(content="first\n"):
    digest = hashlib.sha256(content.encode()).hexdigest()
    file = StoredFile("app.py", "python", content, digest, digest, len(content.encode()), 1)
    return ScanResult((file,), (ManifestEntry("app.py", "b" * 40, file.size_bytes, "stored"),))


def save(connection, sha="a" * 40, version="test-v1", scan_result=None):
    return save_snapshot(
        connection,
        "https://github.com/example/repository",
        sha,
        version,
        scan_result or result(),
        time.monotonic() + 30,
    )


def test_repeated_ingestion_reuses_snapshot_and_preserves_original_text(connection):
    first = save(connection)
    second = save(connection)
    assert first["snapshot_id"] == second["snapshot_id"]
    assert second["reused"] is True
    assert first["status"] == "ingested"
    assert connection.execute(select(func.count()).select_from(repository_files)).scalar_one() == 1
    assert connection.execute(select(repository_files.c.content)).scalar_one() == "first\n"


def test_new_commit_or_policy_preserves_old_snapshot(connection):
    first = save(connection)
    newer = save(connection, sha="c" * 40, scan_result=result("second\n"))
    new_policy = save(connection, version="test-v2")
    assert len({first["snapshot_id"], newer["snapshot_id"], new_policy["snapshot_id"]}) == 3
    content = connection.execute(
        select(repository_files.c.content).where(
            repository_files.c.snapshot_id == first["snapshot_id"]
        )
    ).scalar_one()
    assert content == "first\n"


def test_file_failure_rolls_back_entire_snapshot(connection):
    original = result()
    duplicate = ScanResult(original.files * 2, original.manifest)
    with pytest.raises(IntegrityError), connection.begin_nested():
        save(connection, scan_result=duplicate)
    assert connection.execute(select(func.count()).select_from(snapshots)).scalar_one() == 0
    assert connection.execute(select(func.count()).select_from(repositories)).scalar_one() == 0


def test_expired_operation_does_not_store_anything(connection):
    with pytest.raises(IngestionError):
        save_snapshot(connection, "https://github.com/a/b", "a" * 40, "v1", result(), 0)
    assert connection.execute(select(func.count()).select_from(snapshots)).scalar_one() == 0


def test_changed_content_is_never_written_over_existing_identity(connection):
    first = save(connection)
    second = save(connection, scan_result=result("different\n"))
    assert first["snapshot_id"] == second["snapshot_id"]
    assert connection.execute(select(repository_files.c.content)).scalar_one() == "first\n"


def test_parsing_runs_are_versioned_and_reused(connection):
    snapshot = save(connection, scan_result=result("def f():\n    return 1\n"))
    snapshot_id = UUID(snapshot["snapshot_id"])
    first = parse_and_store(connection, snapshot_id, Settings(_env_file=None))
    second = parse_and_store(connection, snapshot_id, Settings(_env_file=None))
    third = parse_and_store(connection, snapshot_id, Settings(_env_file=None, max_chunk_tokens=4))
    assert first["parsing_run_id"] == second["parsing_run_id"]
    assert second["reused"] is True
    assert third["parsing_run_id"] != first["parsing_run_id"]
    assert first["symbols"] == 1 and first["files_by_status"] == {"parsed": 1}
    assert connection.execute(select(repository_files.c.content)).scalar_one() == (
        "def f():\n    return 1\n"
    )


def test_chunk_limit_rolls_back_partial_run(connection):
    snapshot = save(connection, scan_result=result("def f():\n    return 1\n"))
    settings = Settings(_env_file=None, max_chunk_tokens=4, max_snapshot_chunks=1)
    with pytest.raises(ValueError, match="MAX_SNAPSHOT_CHUNKS"), connection.begin_nested():
        parse_and_store(connection, UUID(snapshot["snapshot_id"]), settings)
    assert connection.execute(select(func.count()).select_from(parsing_runs)).scalar_one() == 0
    assert connection.execute(select(func.count()).select_from(code_chunks)).scalar_one() == 0
    assert connection.execute(select(func.count()).select_from(snapshots)).scalar_one() == 1


def test_parse_failure_is_recorded_and_text_is_chunked(connection):
    snapshot = save(connection, scan_result=result("def broken(:\n"))
    response = parse_and_store(connection, UUID(snapshot["snapshot_id"]), Settings(_env_file=None))
    assert response["files_by_status"] == {"parse_error": 1}
    assert response["chunks"] == 1
    file = connection.execute(select(parsing_runs.c.files)).scalar_one()[0]
    assert file["error"] == "SyntaxError at line 1"
    assert connection.execute(select(code_chunks.c.content)).scalar_one() == "def broken(:\n"
