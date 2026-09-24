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
