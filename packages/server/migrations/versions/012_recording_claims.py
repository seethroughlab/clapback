"""A contribution can name its recording — `ADR-0012` point 1.

Adds `recording_claims`: one row per (fingerprint_hash, recording_mbid, client_id).
The `embeddings` table is untouched. A row's recording is derived from the claims
on its hash — the MBID the most distinct clients have asserted — rather than
written into it, because the server cannot verify an id and the corpus counts
what it cannot verify rather than trusting it (`ADR-0008`).

No data moves and nothing is removed. This is the first migration since `011`
and the first in a while that is additive.

Revision ID: 012_recording_claims
Revises: 011_pipeline_version_is_the_key
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_recording_claims"
down_revision: str | None = "011_pipeline_version_is_the_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recording_claims",
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("recording_mbid", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("fingerprint_hash", "recording_mbid", "client_id"),
    )
    # The primary key already serves "claims for this hash". This serves the
    # other direction — `GET /v1/recordings/{mbid}`.
    op.create_index("ix_recording_claims_mbid", "recording_claims", ["recording_mbid"])


def downgrade() -> None:
    op.drop_index("ix_recording_claims_mbid", table_name="recording_claims")
    op.drop_table("recording_claims")
