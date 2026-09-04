"""Add extended features columns and analysis_details table.

Revision ID: 004_extended_features
Revises: 003_admin
Create Date: 2026-02-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "004_extended_features"
down_revision: str | None = "003_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Phase 1 deep analysis scalars (13 columns)
    op.add_column("features", sa.Column("harmonic_complexity", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("key_stability", sa.String(30), nullable=True))
    op.add_column("features", sa.Column("modal_character", sa.String(50), nullable=True))
    op.add_column("features", sa.Column("modal_confidence", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("swing_ratio", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("syncopation", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("tempo_character", sa.String(30), nullable=True))
    op.add_column("features", sa.Column("brightness", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("dynamic_range_db", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("energy_shape", sa.String(30), nullable=True))
    op.add_column("features", sa.Column("section_count", sa.Integer(), nullable=True))
    op.add_column("features", sa.Column("form_string", sa.String(200), nullable=True))
    op.add_column("features", sa.Column("avg_section_length", sa.Float(), nullable=True))

    # Phase 1 additional scalars (2 columns)
    op.add_column("features", sa.Column("replaygain_track_gain", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("track_peak", sa.Float(), nullable=True))

    # Phase 3 melodic features (3 columns)
    op.add_column("features", sa.Column("note_density", sa.Float(), nullable=True))
    op.add_column("features", sa.Column("interval_character", sa.String(30), nullable=True))
    op.add_column("features", sa.Column("pitch_range", sa.Integer(), nullable=True))

    # Create analysis_details table
    op.create_table(
        "analysis_details",
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column("contributor_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_accessed_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("fingerprint_hash", "analysis_version"),
    )

    op.create_index(
        "ix_analysis_details_last_accessed",
        "analysis_details",
        ["last_accessed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_details_last_accessed", table_name="analysis_details")
    op.drop_table("analysis_details")

    # Drop Phase 3 melodic features
    op.drop_column("features", "pitch_range")
    op.drop_column("features", "interval_character")
    op.drop_column("features", "note_density")

    # Drop Phase 1 additional scalars
    op.drop_column("features", "track_peak")
    op.drop_column("features", "replaygain_track_gain")

    # Drop Phase 1 deep analysis scalars
    op.drop_column("features", "avg_section_length")
    op.drop_column("features", "form_string")
    op.drop_column("features", "section_count")
    op.drop_column("features", "energy_shape")
    op.drop_column("features", "dynamic_range_db")
    op.drop_column("features", "brightness")
    op.drop_column("features", "tempo_character")
    op.drop_column("features", "syncopation")
    op.drop_column("features", "swing_ratio")
    op.drop_column("features", "modal_confidence")
    op.drop_column("features", "modal_character")
    op.drop_column("features", "key_stability")
    op.drop_column("features", "harmonic_complexity")
