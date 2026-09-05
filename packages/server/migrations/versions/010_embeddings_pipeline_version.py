"""Record the pipeline that produced an embedding.

`ADR-0006` phase 1 of point 6: "Server accepts and stores `pipeline_version` as an
optional column. Key unchanged; nothing rejected."

The column the corpus has always needed and never had. Its key is
`(fingerprint_hash, analysis_version, clap_model_version)`, and point 2 of that
record explains why neither version component means what a key needs to mean:
`clap_model_version` is the checkpoint, which pooling can change every vector
without touching, and `analysis_version` is a client-owned counter whose own
history records a bump taken to stay in step with an unrelated feature version.

Nullable, and every existing row is null. That is not a gap to backfill: nobody
recorded which pipeline produced the 21,890 legacy rows, and the 25,596 contributed
this week were sent by a client that did not yet have a field to declare it in.
Phase 2 makes Familiar send it; phase 4 makes it part of the key and rejects
submissions without one.

Indexed because phase 4 will key on it, and because a `WHERE pipeline_version IS
NULL` is how anyone will ask "how much of this corpus predates provenance".

Revision ID: 010_embeddings_pipeline_version
Revises: 009_hnsw_index
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_embeddings_pipeline_version"
down_revision: str | None = "009_hnsw_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `laion/clap-htsat-unfused+frontend1+artifact1+pool1+fp32` is 55 characters. 200
# leaves room for the identity to gain components without a migration, which it
# will: the string is composed from five and nothing bounds that at five.
_LENGTH = 200


def upgrade() -> None:
    op.add_column(
        "embeddings",
        sa.Column("pipeline_version", sa.String(_LENGTH), nullable=True),
    )
    op.create_index("ix_embeddings_pipeline_version", "embeddings", ["pipeline_version"])
    # `submission_agreement` records it too, so a recorded agreement says which
    # pipeline both sides claimed rather than leaving it to be inferred from the
    # embedding row as it stands at some later date.
    op.add_column(
        "submission_agreement",
        sa.Column("pipeline_version", sa.String(_LENGTH), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submission_agreement", "pipeline_version")
    op.drop_index("ix_embeddings_pipeline_version", table_name="embeddings")
    op.drop_column("embeddings", "pipeline_version")
