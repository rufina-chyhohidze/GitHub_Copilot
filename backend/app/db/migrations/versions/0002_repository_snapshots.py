"""Store immutable source snapshots and their complete inclusion/exclusion manifests."""

import sqlalchemy as sa
from alembic import op

revision = "0002_repository_snapshots"
down_revision = "0001_enable_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("canonical_url", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "repository_snapshots",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("repository_id", sa.Uuid, sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("index_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("coverage", sa.JSON, nullable=False),
        sa.Column("manifest", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id", "commit_sha", "index_version", name="uq_snapshot_identity"
        ),
        sa.CheckConstraint(
            "status IN ('ingested', 'indexing', 'ready', 'failed')", name="ck_snapshot_status"
        ),
    )
    op.create_table(
        "repository_files",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("snapshot_id", sa.Uuid, sa.ForeignKey("repository_snapshots.id"), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("line_count", sa.Integer, nullable=False),
        sa.Column("parse_status", sa.String(20), nullable=False),
        sa.UniqueConstraint("snapshot_id", "path", name="uq_snapshot_file_path"),
        sa.CheckConstraint("size_bytes >= 0 AND line_count >= 0", name="ck_file_sizes"),
    )


def downgrade() -> None:
    op.drop_table("repository_files")
    op.drop_table("repository_snapshots")
    op.drop_table("repositories")
