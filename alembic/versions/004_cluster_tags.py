"""Add cluster tags for hashtag-based navigation

Revision ID: 004
Revises: 003
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_clusters",
        sa.Column("tags", sa.String(length=500), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("news_clusters", "tags")
