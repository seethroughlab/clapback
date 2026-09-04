"""Add admin tables for IP banning and statistics.

Revision ID: 003_admin
Revises: 002_features
Create Date: 2026-01-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_admin"
down_revision: str | None = "002_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create banned_ips table
    op.create_table(
        "banned_ips",
        sa.Column("ip_address", sa.String(45), nullable=False),  # IPv6 max length
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "banned_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False
        ),
        sa.Column("banned_by", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("ip_address"),
    )

    # Create ip_stats table for monitoring
    op.create_table(
        "ip_stats",
        sa.Column("ip_address", sa.String(45), nullable=False),
        # Request counts
        sa.Column("total_lookups", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_contributions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lookup_hits", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lookup_misses", sa.Integer(), server_default="0", nullable=False),
        # Timestamps
        sa.Column(
            "first_seen", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False
        ),
        sa.Column(
            "last_seen", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False
        ),
        # Flags
        sa.Column("flagged", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("flag_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("ip_address"),
    )

    # Index for finding flagged IPs
    op.create_index(
        "ix_ip_stats_flagged",
        "ip_stats",
        ["flagged"],
        postgresql_where=sa.text("flagged = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_ip_stats_flagged", table_name="ip_stats")
    op.drop_table("ip_stats")
    op.drop_table("banned_ips")
