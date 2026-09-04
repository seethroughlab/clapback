"""An HNSW index, so the corpus can answer "what else sounds like this".

`ADR-0002` point 1 decided the corpus answers similarity queries and point 2 made
approximate nearest-neighbour search "a requirement of the host, not an
implementation detail". Neither was built: the only index on `embeddings` was its
primary key, `pgvector` appeared once as a column type, and the corpus was a
key-value store spelled in Postgres.

**Cosine, matching how the vectors are used everywhere else.** `clapback-embed`
returns unit-length vectors and `_cosine_similarity` compares them by angle, so
an L2 index would be measuring something the rest of the project does not.

`m` and `ef_construction` are left at pgvector's defaults (16 and 64). `ADR-0003`
point 3 makes RAM the binding constraint and sized the box for an index of
roughly 780 MB at 300,000 vectors; at the current 22,000 the index is a few tens
of megabytes and tuning it would be optimising a number nobody has measured a
problem with.

Revision ID: 009_hnsw_index
Revises: 008_embeddings_client_id
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_hnsw_index"
down_revision: str | None = "008_embeddings_client_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_embeddings_vector_cosine "
        "ON embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_cosine")
