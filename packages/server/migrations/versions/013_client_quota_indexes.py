"""The per-client quota can be counted — `ADR-0016` point 7, `ADR-0004` point 9.

Two indexes and nothing else: `(client_id, created_at)` on `embeddings` and
`(client_id, recorded_at)` on `submission_agreement`, so that "how many rows has
this identifier written in the last 24 hours" is two index range scans per
contribution rather than two table scans. No data moves and nothing is removed.

Revision ID: 013_client_quota_indexes
Revises: 012_recording_claims
Create Date: 2026-09-16
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_client_quota_indexes"
down_revision: str | None = "012_recording_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_embeddings_client_created", "embeddings", ["client_id", "created_at"])
    op.create_index(
        "ix_submission_agreement_client_recorded",
        "submission_agreement",
        ["client_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_submission_agreement_client_recorded", table_name="submission_agreement")
    op.drop_index("ix_embeddings_client_created", table_name="embeddings")
