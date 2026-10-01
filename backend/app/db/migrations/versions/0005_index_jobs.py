"""Persist leased indexing jobs and publish completed snapshot indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0005_index_jobs"
down_revision = "0004_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "index_jobs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("repository_id", sa.Uuid, sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("requested_ref", sa.String(255), nullable=False),
        sa.Column("resolved_commit_sha", sa.String(64)),
        sa.Column("snapshot_id", sa.Uuid, sa.ForeignKey("repository_snapshots.id")),
        sa.Column("parsing_run_id", sa.Uuid, sa.ForeignKey("parsing_runs.id")),
        sa.Column("configuration", sa.JSON, nullable=False),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("lease_token", sa.Uuid),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_job_status"
        ),
        sa.CheckConstraint(
            "stage IN ('queued', 'ingesting', 'parsing', 'embedding', 'publishing', 'complete')",
            name="ck_job_stage",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100 AND attempts >= 0 "
            "AND max_attempts > 0 AND attempts <= max_attempts",
            name="ck_job_limits",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_job_lease",
        ),
    )
    op.create_index("ix_job_claim", "index_jobs", ["status", "available_at", "created_at"])
    op.create_index(
        "uq_active_index_job",
        "index_jobs",
        ["repository_id", "requested_ref", "configuration_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "ready_indexes",
        sa.Column(
            "snapshot_id", sa.Uuid, sa.ForeignKey("repository_snapshots.id"), primary_key=True
        ),
        sa.Column("parsing_run_id", sa.Uuid, primary_key=True),
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parsing_run_id", "profile_id"],
            ["embedding_indexes.parsing_run_id", "embedding_indexes.profile_id"],
        ),
    )


def downgrade():
    op.drop_table("ready_indexes")
    op.drop_table("index_jobs")
