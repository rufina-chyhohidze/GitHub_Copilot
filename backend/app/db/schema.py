"""Persist immutable source, versioned indexes, and recoverable indexing jobs."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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

embedding_profiles = Table(
    "embedding_profiles",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("provider", String(32), nullable=False),
    Column("model_id", Text, nullable=False),
    Column("model_version", Text, nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("input_version", String(32), nullable=False),
)
embeddings = Table(
    "embeddings",
    metadata,
    Column("profile_id", String(64), ForeignKey("embedding_profiles.id"), primary_key=True),
    Column("input_hash", String(64), primary_key=True),
    Column("vector", Vector(), nullable=False),
)
chunk_embeddings = Table(
    "chunk_embeddings",
    metadata,
    Column("chunk_id", Uuid, ForeignKey("code_chunks.id"), primary_key=True),
    Column("profile_id", String(64), primary_key=True),
    Column("input_hash", String(64), nullable=False),
    ForeignKeyConstraint(
        ["profile_id", "input_hash"], ["embeddings.profile_id", "embeddings.input_hash"]
    ),
)
embedding_indexes = Table(
    "embedding_indexes",
    metadata,
    Column("parsing_run_id", Uuid, ForeignKey("parsing_runs.id"), primary_key=True),
    Column("profile_id", String(64), ForeignKey("embedding_profiles.id"), primary_key=True),
    Column("chunk_count", Integer, nullable=False),
)

index_jobs = Table(
    "index_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("repository_id", Uuid, ForeignKey("repositories.id"), nullable=False),
    Column("requested_ref", String(255), nullable=False),
    Column("resolved_commit_sha", String(64)),
    Column("snapshot_id", Uuid, ForeignKey("repository_snapshots.id")),
    Column("parsing_run_id", Uuid, ForeignKey("parsing_runs.id")),
    Column("configuration", JSON, nullable=False),
    Column("configuration_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("stage", String(20), nullable=False),
    Column("progress", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("lease_token", Uuid),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("error", JSON),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_job_status"),
    CheckConstraint(
        "stage IN ('queued', 'ingesting', 'parsing', 'embedding', 'publishing', 'complete')",
        name="ck_job_stage",
    ),
    CheckConstraint(
        "progress BETWEEN 0 AND 100 AND attempts >= 0 "
        "AND max_attempts > 0 AND attempts <= max_attempts",
        name="ck_job_limits",
    ),
    CheckConstraint(
        "(status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="ck_job_lease",
    ),
)
Index("ix_job_claim", index_jobs.c.status, index_jobs.c.available_at, index_jobs.c.created_at)
Index(
    "uq_active_index_job",
    index_jobs.c.repository_id,
    index_jobs.c.requested_ref,
    index_jobs.c.configuration_hash,
    unique=True,
    postgresql_where=index_jobs.c.status.in_(["queued", "running"]),
)

ready_indexes = Table(
    "ready_indexes",
    metadata,
    Column("snapshot_id", Uuid, ForeignKey("repository_snapshots.id"), primary_key=True),
    Column("parsing_run_id", Uuid, primary_key=True),
    Column("profile_id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["parsing_run_id", "profile_id"],
        ["embedding_indexes.parsing_run_id", "embedding_indexes.profile_id"],
    ),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("snapshot_id", Uuid, nullable=False),
    Column("parsing_run_id", Uuid, nullable=False),
    Column("profile_id", String(64), nullable=False),
    Column("title", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["snapshot_id", "parsing_run_id", "profile_id"],
        ["ready_indexes.snapshot_id", "ready_indexes.parsing_run_id", "ready_indexes.profile_id"],
    ),
)
messages = Table(
    "messages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("conversation_id", Uuid, ForeignKey("conversations.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
)
answer_runs = Table(
    "answer_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("conversation_id", Uuid, ForeignKey("conversations.id"), nullable=False),
    Column("input_message_id", Uuid, ForeignKey("messages.id"), nullable=False, unique=True),
    Column("output_message_id", Uuid, ForeignKey("messages.id"), unique=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("pipeline", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("model_id", Text),
    Column("limits", JSON),
    Column("usage", JSON),
    Column("error", String(64)),
    Column("sequence", Integer, nullable=False),
    Column("lease_token", Uuid),
    Column("expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("conversation_id", "idempotency_key", name="uq_message_idempotency"),
    CheckConstraint("pipeline IN ('fixed', 'agent')", name="ck_answer_pipeline"),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')",
        name="ck_answer_status",
    ),
)
Index("ix_answer_claim", answer_runs.c.status, answer_runs.c.created_at)
Index(
    "uq_active_conversation_run",
    answer_runs.c.conversation_id,
    unique=True,
    postgresql_where=answer_runs.c.status.in_(["queued", "running"]),
)
run_evidence = Table(
    "run_evidence",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("run_id", Uuid, ForeignKey("answer_runs.id"), nullable=False),
    Column("file_id", Uuid, ForeignKey("repository_files.id"), nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("originating_tool", String(64), nullable=False),
    Column("citation", JSON, nullable=False),
    CheckConstraint("start_line >= 1 AND end_line >= start_line", name="ck_run_evidence_span"),
)
run_events = Table(
    "run_events",
    metadata,
    Column("run_id", Uuid, ForeignKey("answer_runs.id"), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("type", String(32), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("sequence > 0", name="ck_event_sequence"),
)
