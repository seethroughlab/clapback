"""Record how closely independent submissions agree, instead of discarding them.

`contribute_embedding` increments `contributor_count` on a conflicting POST and
throws the submitted vector away. So the question a verified commons rests on —
*do two machines computing the same audio produce the same vector?* — has no data
behind it, despite 44 contributing installations and 21,890 stored embeddings.

This table starts collecting that. It records only: nothing is gated, nothing is
served differently, and first-write-wins is unchanged.

Revision ID: 007_submission_agreement
Revises: 006_index_embeddings_created_at
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_submission_agreement"
down_revision: str | None = "006_index_embeddings_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_agreement",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("clap_model_version", sa.String(100), nullable=False),
        # The measurement. 1.0 is identical.
        sa.Column("similarity", sa.Float(), nullable=False),
        # Nullable: existing clients do not send one and must keep working. Without
        # it, a "second submission" may just be one client retrying.
        sa.Column("client_id", sa.String(64), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_submission_agreement_recorded_at", "submission_agreement", ["recorded_at"]
    )
    op.create_index(
        "ix_submission_agreement_key",
        "submission_agreement",
        ["fingerprint_hash", "analysis_version", "clap_model_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_submission_agreement_key", table_name="submission_agreement")
    op.drop_index(
        "ix_submission_agreement_recorded_at", table_name="submission_agreement"
    )
    op.drop_table("submission_agreement")
