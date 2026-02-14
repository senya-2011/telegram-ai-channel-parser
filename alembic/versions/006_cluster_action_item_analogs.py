"""Add action item and analogs to news clusters

Revision ID: 006
Revises: 005
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_clusters",
        sa.Column("analogs", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "news_clusters",
        sa.Column("action_item", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("news_clusters", "action_item")
    op.drop_column("news_clusters", "analogs")
