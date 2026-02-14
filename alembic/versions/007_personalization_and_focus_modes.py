"""Add personalization and focus-mode fields

Revision ID: 007
Revises: 006
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_clusters",
        sa.Column("implementable_by_small_team", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "news_clusters",
        sa.Column("infra_barrier", sa.String(length=10), server_default=sa.text("'high'"), nullable=False),
    )
    op.add_column(
        "alerts",
        sa.Column("user_relevance_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column("include_tech_updates", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "user_settings",
        sa.Column("include_industry_reports", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "user_settings",
        sa.Column("user_prompt", sa.Text(), nullable=True),
    )

    op.create_table(
        "user_news_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("vote", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["news_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "cluster_id", name="uq_user_cluster_feedback"),
    )
    op.create_index(op.f("ix_user_news_feedback_user_id"), "user_news_feedback", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_news_feedback_cluster_id"), "user_news_feedback", ["cluster_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_news_feedback_cluster_id"), table_name="user_news_feedback")
    op.drop_index(op.f("ix_user_news_feedback_user_id"), table_name="user_news_feedback")
    op.drop_table("user_news_feedback")

    op.drop_column("user_settings", "user_prompt")
    op.drop_column("user_settings", "include_industry_reports")
    op.drop_column("user_settings", "include_tech_updates")
    op.drop_column("alerts", "user_relevance_score")
    op.drop_column("news_clusters", "infra_barrier")
    op.drop_column("news_clusters", "implementable_by_small_team")
