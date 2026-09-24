"""Source storage is independent of future embedding and conversation tables."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)

metadata = MetaData()

repositories = Table(
    "repositories",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("canonical_url", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

snapshots = Table(
    "repository_snapshots",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("repository_id", Uuid, ForeignKey("repositories.id"), nullable=False),
    Column("commit_sha", String(64), nullable=False),
    Column("index_version", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("coverage", JSON, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("repository_id", "commit_sha", "index_version", name="uq_snapshot_identity"),
    CheckConstraint(
        "status IN ('ingested', 'indexing', 'ready', 'failed')", name="ck_snapshot_status"
    ),
)

repository_files = Table(
    "repository_files",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("snapshot_id", Uuid, ForeignKey("repository_snapshots.id"), nullable=False),
    Column("path", Text, nullable=False),
    Column("language", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("raw_hash", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("line_count", Integer, nullable=False),
    Column("parse_status", String(20), nullable=False),
    UniqueConstraint("snapshot_id", "path", name="uq_snapshot_file_path"),
    CheckConstraint("size_bytes >= 0 AND line_count >= 0", name="ck_file_sizes"),
)

parsing_runs = Table(
    "parsing_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("snapshot_id", Uuid, ForeignKey("repository_snapshots.id"), nullable=False),
    Column("pipeline_version", String(64), nullable=False),
    Column("parser_version", String(64), nullable=False),
    Column("chunker_version", String(64), nullable=False),
    Column("max_chunk_tokens", Integer, nullable=False),
    Column("files", JSON, nullable=False),
    Column("summary", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("snapshot_id", "pipeline_version", name="uq_parsing_run_version"),
)

code_chunks = Table(
    "code_chunks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("parsing_run_id", Uuid, ForeignKey("parsing_runs.id"), nullable=False),
    Column("file_id", Uuid, ForeignKey("repository_files.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    Column("start_char", Integer, nullable=False),
    Column("end_char", Integer, nullable=False),
    Column("symbol_name", Text),
    Column("symbol_type", String(20)),
    Column("parent_name", Text),
    Column("token_upper_bound", Integer, nullable=False),
    UniqueConstraint("parsing_run_id", "file_id", "ordinal", name="uq_chunk_ordinal"),
    CheckConstraint(
        "start_line >= 1 AND end_line >= start_line AND start_char >= 0 "
        "AND end_char > start_char AND token_upper_bound > 0",
        name="ck_chunk_span",
    ),
)
