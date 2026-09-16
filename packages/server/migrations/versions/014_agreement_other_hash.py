"""An agreement names the row it was compared against — `ADR-0019` point 2.

Until now a submission was only ever compared with the row under its own key,
so `submission_agreement.fingerprint_hash` named both the submission and the
stored row. `ADR-0019` compares a submission that names its recording with
every row under the same pipeline that any client has claimed under that
recording — across keys — so the two are no longer the same hash.
`fingerprint_hash` keeps meaning the submitted key; `other_hash` names the
stored row the similarity was measured against. Existing rows are backfilled
with their own hash, which is what they were.

Revision ID: 014_agreement_other_hash
Revises: 013_client_quota_indexes
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_agreement_other_hash"
down_revision: str | None = "013_client_quota_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("submission_agreement", sa.Column("other_hash", sa.String(64), nullable=True))
    op.execute("UPDATE submission_agreement SET other_hash = fingerprint_hash WHERE other_hash IS NULL")
    op.alter_column("submission_agreement", "other_hash", nullable=False)
    # The read side: "what was measured against this row, under this pipeline".
    op.create_index(
        "ix_submission_agreement_other",
        "submission_agreement",
        ["other_hash", "pipeline_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_submission_agreement_other", table_name="submission_agreement")
    op.drop_column("submission_agreement", "other_hash")
