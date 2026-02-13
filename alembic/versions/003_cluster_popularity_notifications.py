"""Track cluster popularity notification state

Revision ID: 003
Revises: 002
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_clusters",
        sa.Column("popularity_notified_mentions", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("news_clusters", "popularity_notified_mentions")
