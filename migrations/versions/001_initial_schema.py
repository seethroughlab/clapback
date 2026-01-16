"""Initial schema with embeddings table.

Revision ID: 001_initial
Revises:
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create embeddings table
    op.create_table(
        "embeddings",
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("clap_model_version", sa.String(100), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column(
            "contributor_count", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False
        ),
        sa.Column(
            "last_accessed_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "fingerprint_hash", "analysis_version", "clap_model_version"
        ),
    )

    # Create index on last_accessed_at for cleanup queries
    op.create_index(
        "ix_embeddings_last_accessed_at",
        "embeddings",
        ["last_accessed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_embeddings_last_accessed_at", table_name="embeddings")
    op.drop_table("embeddings")
