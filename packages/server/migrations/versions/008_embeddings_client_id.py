"""Record which installation contributed an embedding.

`ADR-0004` point 7 requires deletion "by fingerprint hash and by client
identifier", and point 6 requires revocation to cascade over a client's
submissions. Neither is implementable against the schema as it stood: only
`submission_agreement` carried a `client_id`, and only for *duplicate*
submissions — the row that actually created an embedding recorded nothing about
who sent it.

So a takedown by hash was possible and a revocation by client was not, which
made point 6 a promise the schema could not keep.

Nullable, and stays nullable. Every existing row predates the field and no
backfill is honest — nobody recorded who contributed those 21,890 embeddings, and
inventing an answer is worse than admitting there isn't one. `ADR-0004` point 3
already decided what an unattributed submission is worth: accepted, stored, and
never confirmable.

Revision ID: 008_embeddings_client_id
Revises: 007_submission_agreement
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_embeddings_client_id"
down_revision: str | None = "007_submission_agreement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column("client_id", sa.String(64), nullable=True),
    )
    # Revocation deletes every row for one client, so the index is on the column
    # the delete filters by rather than on anything a lookup uses.
    op.create_index(
        "ix_embeddings_client_id",
        "embeddings",
        ["client_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_embeddings_client_id", table_name="embeddings")
    op.drop_column("embeddings", "client_id")
