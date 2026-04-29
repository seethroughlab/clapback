"""Index embeddings.created_at for the public dashboard.

The /-route runs velocity (`created_at > now() - interval '7 days'`) and
growth (`GROUP BY date_trunc('day', created_at)`) queries that scan
embeddings.created_at on every cache miss. Even at 22K rows the index
turns those into bounded range scans.

Revision ID: 006_index_embeddings_created_at
Revises: 005_features_jsonb
Create Date: 2026-04-29
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_index_embeddings_created_at"
down_revision: Union[str, None] = "005_features_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_embeddings_created_at",
        "embeddings",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_embeddings_created_at", table_name="embeddings")
