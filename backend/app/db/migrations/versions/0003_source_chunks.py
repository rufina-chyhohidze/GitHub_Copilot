"""Version parsed metadata and source chunks without replacing original source."""

import sqlalchemy as sa
from alembic import op

revision = "0003_source_chunks"
down_revision = "0002_repository_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parsing_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("snapshot_id", sa.Uuid, sa.ForeignKey("repository_snapshots.id"), nullable=False),
        sa.Column("pipeline_version", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(64), nullable=False),
        sa.Column("max_chunk_tokens", sa.Integer, nullable=False),
        sa.Column("files", sa.JSON, nullable=False),
        sa.Column("summary", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "pipeline_version", name="uq_parsing_run_version"),
    )
    op.create_table(
        "code_chunks",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("parsing_run_id", sa.Uuid, sa.ForeignKey("parsing_runs.id"), nullable=False),
        sa.Column("file_id", sa.Uuid, sa.ForeignKey("repository_files.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
        sa.Column("start_char", sa.Integer, nullable=False),
        sa.Column("end_char", sa.Integer, nullable=False),
        sa.Column("symbol_name", sa.Text),
        sa.Column("symbol_type", sa.String(20)),
        sa.Column("parent_name", sa.Text),
        sa.Column("token_upper_bound", sa.Integer, nullable=False),
        sa.UniqueConstraint("parsing_run_id", "file_id", "ordinal", name="uq_chunk_ordinal"),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line AND start_char >= 0 "
            "AND end_char > start_char AND token_upper_bound > 0",
            name="ck_chunk_span",
        ),
    )


def downgrade() -> None:
    op.drop_table("code_chunks")
    op.drop_table("parsing_runs")
