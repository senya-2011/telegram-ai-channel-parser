"""Add news clusters and post-level dedup metadata

Revision ID: 002
Revises: 001
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_clusters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("canonical_summary", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("is_ai_relevant", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("mention_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source_ids", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("coreai_score", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("coreai_reason", sa.Text(), nullable=True),
        sa.Column("alert_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("canonical_hash", name="uq_news_clusters_canonical_hash"),
    )
    op.create_index("ix_news_clusters_canonical_hash", "news_clusters", ["canonical_hash"], unique=True)

    op.add_column("posts", sa.Column("cluster_id", sa.Integer(), nullable=True))
    op.add_column("posts", sa.Column("normalized_hash", sa.String(length=64), nullable=True))
    op.add_column("posts", sa.Column("is_ai_relevant", sa.Boolean(), nullable=True))
    op.create_index("ix_posts_cluster_id", "posts", ["cluster_id"])
    op.create_index("ix_posts_normalized_hash", "posts", ["normalized_hash"])
    op.create_foreign_key(
        "fk_posts_cluster_id_news_clusters",
        "posts",
        "news_clusters",
        ["cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_posts_cluster_id_news_clusters", "posts", type_="foreignkey")
    op.drop_index("ix_posts_normalized_hash", table_name="posts")
    op.drop_index("ix_posts_cluster_id", table_name="posts")
    op.drop_column("posts", "is_ai_relevant")
    op.drop_column("posts", "normalized_hash")
    op.drop_column("posts", "cluster_id")

    op.drop_index("ix_news_clusters_canonical_hash", table_name="news_clusters")
    op.drop_table("news_clusters")
