"""A row says which kind of key it has — `ADR-0020` point 2.

Every row so far is keyed on the SHA256 of an AcoustID fingerprint. `ADR-0020`
admits a contribution that carries a MusicBrainz recording id and no
fingerprint, keyed on `SHA256("musicbrainz_recording:" + mbid)` in the same
column — a digest of the same shape, disjoint from every fingerprint hash, so
the primary key and every index are untouched. What a reader needs is to tell
the two apart: `key_type` is `fingerprint` (every existing row, hence the
default) or `musicbrainz_recording`, and every read that serves a row serves
it. Additive; touches no existing row.

Revision ID: 016_key_type
Revises: 015_claim_type
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016_key_type"
down_revision: str | None = "015_claim_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column("key_type", sa.String(32), nullable=False, server_default="fingerprint"),
    )


def downgrade() -> None:
    # A recording-keyed row has no meaning to a schema that cannot say what its
    # key is; its claims and agreements go with it, as a takedown's would.
    op.execute(
        "DELETE FROM submission_agreement WHERE fingerprint_hash IN "
        "(SELECT fingerprint_hash FROM embeddings WHERE key_type <> 'fingerprint') "
        "OR other_hash IN (SELECT fingerprint_hash FROM embeddings WHERE key_type <> 'fingerprint')"
    )
    op.execute(
        "DELETE FROM recording_claims WHERE fingerprint_hash IN "
        "(SELECT fingerprint_hash FROM embeddings WHERE key_type <> 'fingerprint')"
    )
    op.execute("DELETE FROM embeddings WHERE key_type <> 'fingerprint'")
    op.drop_column("embeddings", "key_type")
