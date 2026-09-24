"""Cache versioned embeddings and publish complete searchable chunk indexes."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004_embeddings"
down_revision = "0003_source_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("input_version", sa.String(32), nullable=False),
    )
    op.create_table(
        "embeddings",
        sa.Column(
            "profile_id", sa.String(64), sa.ForeignKey("embedding_profiles.id"), primary_key=True
        ),
        sa.Column("input_hash", sa.String(64), primary_key=True),
        sa.Column("vector", Vector(), nullable=False),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid, sa.ForeignKey("code_chunks.id"), primary_key=True),
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id", "input_hash"], ["embeddings.profile_id", "embeddings.input_hash"]
        ),
    )
    op.create_table(
        "embedding_indexes",
        sa.Column("parsing_run_id", sa.Uuid, sa.ForeignKey("parsing_runs.id"), primary_key=True),
        sa.Column(
            "profile_id", sa.String(64), sa.ForeignKey("embedding_profiles.id"), primary_key=True
        ),
        sa.Column("chunk_count", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("embedding_indexes")
    op.drop_table("chunk_embeddings")
    op.drop_table("embeddings")
    op.drop_table("embedding_profiles")
