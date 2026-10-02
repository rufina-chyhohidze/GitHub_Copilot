"""Persist conversations and resumable answer runs."""

from alembic import op
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

revision = "0006_conversations"
down_revision = "0005_index_jobs"
branch_labels = None
depends_on = None

# Resolve existing foreign keys without managing their tables.
Table(
    "ready_indexes",
    metadata,
    Column("snapshot_id", Uuid),
    Column("parsing_run_id", Uuid),
    Column("profile_id", String(64)),
)
Table("repository_files", metadata, Column("id", Uuid))

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


def upgrade():
    for table in (conversations, messages, answer_runs, run_evidence, run_events):
        table.create(op.get_bind())


def downgrade():
    for table in (run_events, run_evidence, answer_runs, messages, conversations):
        table.drop(op.get_bind())
