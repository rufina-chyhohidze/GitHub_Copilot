"""Persist completed parsing runs atomically, keeping old versions available."""

import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine

from app.config import Settings
from app.db.schema import code_chunks, parsing_runs, repository_files, snapshots
from app.ingestion.chunker import CHUNKER_VERSION, chunks_for_source, pipeline_version
from app.ingestion.parser import PARSER_VERSION, parse_source


def parse_and_store(connection: Connection, snapshot_id: UUID, settings: Settings) -> dict:
    deadline = time.monotonic() + settings.parsing_timeout_seconds

    def check_time():
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise ValueError("Parsing exceeded COPILOT_PARSING_TIMEOUT_SECONDS")
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": str(remaining)},
        )

    check_time()
    found = connection.execute(
        select(snapshots.c.id).where(snapshots.c.id == snapshot_id).with_for_update()
    ).scalar_one_or_none()
    if found is None:
        raise ValueError("Snapshot not found; ingest a repository first")
    version = pipeline_version(settings.max_chunk_tokens)
    existing = (
        connection.execute(
            select(parsing_runs).where(
                parsing_runs.c.snapshot_id == snapshot_id,
                parsing_runs.c.pipeline_version == version,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        return {
            "snapshot_id": str(snapshot_id),
            "parsing_run_id": str(existing["id"]),
            "pipeline_version": version,
            "reused": True,
            **existing["summary"],
        }

    run_id = uuid4()
    connection.execute(
        parsing_runs.insert().values(
            id=run_id,
            snapshot_id=snapshot_id,
            pipeline_version=version,
            parser_version=PARSER_VERSION,
            chunker_version=CHUNKER_VERSION,
            max_chunk_tokens=settings.max_chunk_tokens,
            files=[],
            summary={},
            created_at=datetime.now(UTC),
        )
    )
    file_ids = (
        connection.execute(
            select(repository_files.c.id)
            .where(repository_files.c.snapshot_id == snapshot_id)
            .order_by(repository_files.c.path)
        )
        .scalars()
        .all()
    )
    details = []
    statuses = Counter()
    total_chunks = total_symbols = total_imports = 0
    for file_id in file_ids:
        check_time()
        file = (
            connection.execute(select(repository_files).where(repository_files.c.id == file_id))
            .mappings()
            .one()
        )
        parsed = parse_source(file["content"], file["language"])
        chunks = chunks_for_source(
            file["content"],
            parsed,
            settings.max_chunk_tokens,
            max_chunks=settings.max_snapshot_chunks - total_chunks,
        )
        check_time()
        total_chunks += len(chunks)
        if total_chunks > settings.max_snapshot_chunks:
            raise ValueError("Parsing exceeded COPILOT_MAX_SNAPSHOT_CHUNKS")
        total_symbols += len(parsed.symbols)
        total_imports += len(parsed.imports)
        statuses[parsed.status] += 1
        details.append(
            {
                "file_id": str(file_id),
                "path": file["path"],
                "status": parsed.status,
                "error": parsed.error,
                "symbols": [asdict(symbol) for symbol in parsed.symbols],
                "imports": [asdict(item) for item in parsed.imports],
                "chunk_count": len(chunks),
            }
        )
        for offset in range(0, len(chunks), 100):
            check_time()
            connection.execute(
                code_chunks.insert(),
                [
                    {
                        "id": uuid4(),
                        "parsing_run_id": run_id,
                        "file_id": file_id,
                        "ordinal": offset + index,
                        **asdict(chunk),
                    }
                    for index, chunk in enumerate(chunks[offset : offset + 100])
                ],
            )
    summary = {
        "files": len(details),
        "symbols": total_symbols,
        "imports": total_imports,
        "chunks": total_chunks,
        "files_by_status": dict(statuses),
        "ready_for_qa": False,
    }
    check_time()
    connection.execute(
        parsing_runs.update()
        .where(parsing_runs.c.id == run_id)
        .values(
            files=details,
            summary=summary,
        )
    )
    check_time()
    return {
        "snapshot_id": str(snapshot_id),
        "parsing_run_id": str(run_id),
        "pipeline_version": version,
        "reused": False,
        **summary,
    }


def parse_snapshot(snapshot_id: UUID, settings: Settings, engine: Engine) -> dict:
    with engine.begin() as connection:
        return parse_and_store(connection, snapshot_id, settings)
