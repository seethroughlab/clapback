"""Add features table for caching audio features.

Revision ID: 002_features
Revises: 001_initial
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_features"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create features table
    op.create_table(
        "features",
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        # Audio features
        sa.Column("bpm", sa.Float(), nullable=True),
        sa.Column("key", sa.String(10), nullable=True),
        sa.Column("energy", sa.Float(), nullable=True),
        sa.Column("danceability", sa.Float(), nullable=True),
        sa.Column("valence", sa.Float(), nullable=True),
        sa.Column("acousticness", sa.Float(), nullable=True),
        sa.Column("instrumentalness", sa.Float(), nullable=True),
        sa.Column("speechiness", sa.Float(), nullable=True),
        sa.Column("liveness", sa.Float(), nullable=True),
        sa.Column("loudness", sa.Float(), nullable=True),
        # Metadata
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
        sa.PrimaryKeyConstraint("fingerprint_hash", "analysis_version"),
    )

    # Create index on last_accessed_at for cleanup queries
    op.create_index(
        "ix_features_last_accessed_at",
        "features",
        ["last_accessed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_features_last_accessed_at", table_name="features")
    op.drop_table("features")
