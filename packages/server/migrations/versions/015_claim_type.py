"""A claim says what kind of id it is — `ADR-0019` point 6.

`recording_claims` held one kind of identifier, the MusicBrainz recording MBID,
in a column named for it. `ADR-0019` point 6 admits a second, the AcoustID
track id — what AcoustID's own matcher assigns to a cluster of near-identical
fingerprints, the same across decoders, `fpcalc` versions and lossy re-encodes
of one rip — so that point 2's cross-key join can run on either. Both are UUID
text, so the column's shape is unchanged; its name is not, because a column
called `recording_mbid` holding something that is not one is the trap
`ADR-0012` names beets' `mb_trackid` for. `recording_id` holds the id and
`claim_type` says which kind: `musicbrainz_recording` (every existing row) or
`acoustid_track`. The type is part of the key.

Revision ID: 015_claim_type
Revises: 014_agreement_other_hash
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_claim_type"
down_revision: str | None = "014_agreement_other_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("recording_claims", "recording_mbid", new_column_name="recording_id")
    op.add_column(
        "recording_claims",
        sa.Column("claim_type", sa.String(32), nullable=False, server_default="musicbrainz_recording"),
    )
    op.drop_constraint("recording_claims_pkey", "recording_claims", type_="primary")
    op.create_primary_key(
        "recording_claims_pkey",
        "recording_claims",
        ["fingerprint_hash", "claim_type", "recording_id", "client_id"],
    )
    op.drop_index("ix_recording_claims_mbid", table_name="recording_claims")
    # "What does recording X sound like" — now per kind of id.
    op.create_index("ix_recording_claims_id", "recording_claims", ["claim_type", "recording_id"])


def downgrade() -> None:
    op.execute("DELETE FROM recording_claims WHERE claim_type <> 'musicbrainz_recording'")
    op.drop_index("ix_recording_claims_id", table_name="recording_claims")
    op.drop_constraint("recording_claims_pkey", "recording_claims", type_="primary")
    op.drop_column("recording_claims", "claim_type")
    op.alter_column("recording_claims", "recording_id", new_column_name="recording_mbid")
    op.create_primary_key(
        "recording_claims_pkey", "recording_claims", ["fingerprint_hash", "recording_mbid", "client_id"]
    )
    op.create_index("ix_recording_claims_mbid", "recording_claims", ["recording_mbid"])
