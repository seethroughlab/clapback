"""Replace 28 typed feature columns with a single features JSONB column.

The server is a dumb key-value store — it never queries by individual
features.  A JSONB blob eliminates schema coupling so the client can
evolve its analysis pipeline without requiring server migrations.

Revision ID: 005_features_jsonb
Revises: 004_extended_features
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "005_features_jsonb"
down_revision: Union[str, None] = "004_extended_features"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The 28 typed columns being replaced
_FEATURE_COLUMNS = [
    "bpm", "key", "energy", "danceability", "valence", "acousticness",
    "instrumentalness", "speechiness", "liveness", "loudness",
    "harmonic_complexity", "key_stability", "modal_character", "modal_confidence",
    "swing_ratio", "syncopation", "tempo_character", "brightness",
    "dynamic_range_db", "energy_shape", "section_count", "form_string",
    "avg_section_length", "replaygain_track_gain", "track_peak",
    "note_density", "interval_character", "pitch_range",
]


def upgrade() -> None:
    # 1. Add the new JSONB column (nullable initially so we can populate it)
    op.add_column(
        "features",
        sa.Column("features", JSONB(), nullable=True),
    )

    # 2. Pack existing typed columns into the JSONB blob using a single SQL
    #    UPDATE.  build_object pairs are (key_literal, column_ref, ...).
    #    We use jsonb_strip_nulls to omit columns that were NULL.
    pairs = ", ".join(
        f"'{col}', {col}" for col in _FEATURE_COLUMNS
    )
    op.execute(
        f"UPDATE features SET features = jsonb_strip_nulls(jsonb_build_object({pairs}))"
    )

    # 3. Rows with all-NULL features end up as '{}' — that's fine.
    #    Set NOT NULL + default now that every row has a value.
    op.alter_column(
        "features", "features",
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )

    # 4. Drop the 28 typed columns
    for col in _FEATURE_COLUMNS:
        op.drop_column("features", col)


def downgrade() -> None:
    # Re-create the 28 typed columns
    _col_types = {
        "bpm": sa.Float(),
        "key": sa.String(10),
        "energy": sa.Float(),
        "danceability": sa.Float(),
        "valence": sa.Float(),
        "acousticness": sa.Float(),
        "instrumentalness": sa.Float(),
        "speechiness": sa.Float(),
        "liveness": sa.Float(),
        "loudness": sa.Float(),
        "harmonic_complexity": sa.Float(),
        "key_stability": sa.String(30),
        "modal_character": sa.String(50),
        "modal_confidence": sa.Float(),
        "swing_ratio": sa.Float(),
        "syncopation": sa.Float(),
        "tempo_character": sa.String(30),
        "brightness": sa.Float(),
        "dynamic_range_db": sa.Float(),
        "energy_shape": sa.String(30),
        "section_count": sa.Integer(),
        "form_string": sa.String(200),
        "avg_section_length": sa.Float(),
        "replaygain_track_gain": sa.Float(),
        "track_peak": sa.Float(),
        "note_density": sa.Float(),
        "interval_character": sa.String(30),
        "pitch_range": sa.Integer(),
    }

    for col, col_type in _col_types.items():
        op.add_column("features", sa.Column(col, col_type, nullable=True))

    # Unpack JSONB back into typed columns
    set_clauses = ", ".join(
        f"{col} = (features->>'{col}')::{_pg_cast(col_type)}"
        for col, col_type in _col_types.items()
    )
    op.execute(f"UPDATE features SET {set_clauses}")

    # Drop the JSONB column
    op.drop_column("features", "features")


def _pg_cast(sa_type: sa.types.TypeEngine) -> str:
    """Map SQLAlchemy type to PostgreSQL cast target."""
    if isinstance(sa_type, sa.Float):
        return "double precision"
    if isinstance(sa_type, sa.Integer):
        return "integer"
    if isinstance(sa_type, sa.String):
        return "text"
    return "text"
