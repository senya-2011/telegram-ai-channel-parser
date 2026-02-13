"""Add product-first relevance fields for clusters

Revision ID: 005
Revises: 004
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_clusters",
        sa.Column("news_kind", sa.String(length=20), server_default="misc", nullable=False),
    )
    op.add_column(
        "news_clusters",
        sa.Column("product_score", sa.Float(), server_default="0.0", nullable=False),
    )
    op.add_column(
        "news_clusters",
        sa.Column("priority", sa.String(length=10), server_default="low", nullable=False),
    )
    op.add_column(
        "news_clusters",
        sa.Column("is_alert_worthy", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("news_clusters", "is_alert_worthy")
    op.drop_column("news_clusters", "priority")
    op.drop_column("news_clusters", "product_score")
    op.drop_column("news_clusters", "news_kind")
