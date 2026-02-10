"""Initial migration

Revision ID: 001
Revises:
Create Date: 2026-02-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "user_telegram_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), unique=True, nullable=False, index=True),
    )

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("digest_time", sa.String(5), default="20:00"),
        sa.Column("timezone", sa.String(50), default="Europe/Moscow"),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("identifier", sa.String(500), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("is_default", sa.Boolean(), default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("type", "identifier", name="uq_source_type_identifier"),
        sa.Index("ix_source_type", "type"),
    )

    op.create_table(
        "user_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "source_id", name="uq_user_source"),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("external_id", sa.String(500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("reactions_count", sa.Integer(), default=0, nullable=False),
        sa.Column("reactions_ratio", sa.Float(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_source_external_id"),
        sa.Index("ix_post_published_at", "published_at"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("is_sent", sa.Boolean(), default=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("posts")
    op.drop_table("user_sources")
    op.drop_table("sources")
    op.drop_table("user_settings")
    op.drop_table("user_telegram_links")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
