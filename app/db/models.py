import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    telegram_links = relationship("UserTelegramLink", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    sources = relationship("UserSource", back_populates="user", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="user", cascade="all, delete-orphan")


class UserTelegramLink(Base):
    __tablename__ = "user_telegram_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)

    user = relationship("User", back_populates="telegram_links")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    digest_time = Column(String(5), default="20:00")  # HH:MM format
    timezone = Column(String(50), default="Europe/Moscow")
    include_tech_updates = Column(Boolean, default=False, nullable=False)
    include_industry_reports = Column(Boolean, default=False, nullable=False)
    user_prompt = Column(Text, nullable=True)

    user = relationship("User", back_populates="settings")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)  # 'telegram' | 'web' | 'reddit' | 'github' | 'producthunt'
    identifier = Column(String(500), nullable=False)  # @channel or URL
    title = Column(String(500), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("type", "identifier", name="uq_source_type_identifier"),
        Index("ix_source_type", "type"),
    )

    user_sources = relationship("UserSource", back_populates="source", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="source", cascade="all, delete-orphan")


class UserSource(Base):
    __tablename__ = "user_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_user_source"),
    )

    user = relationship("User", back_populates="sources")
    source = relationship("Source", back_populates="user_sources")


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_id = Column(Integer, ForeignKey("news_clusters.id", ondelete="SET NULL"), nullable=True, index=True)
    external_id = Column(String(500), nullable=True)  # message_id or article URL
    content = Column(Text, nullable=False)
    normalized_hash = Column(String(64), nullable=True, index=True)
    summary = Column(Text, nullable=True)
    embedding = Column(Vector(384), nullable=True)  # all-MiniLM-L6-v2 outputs 384-dim
    is_ai_relevant = Column(Boolean, nullable=True)
    reactions_count = Column(Integer, default=0, nullable=False)
    reactions_ratio = Column(Float, nullable=True)  # ratio vs avg for channel
    published_at = Column(DateTime, nullable=True)
    parsed_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_external_id"),
        Index("ix_post_published_at", "published_at"),
    )

    source = relationship("Source", back_populates="posts")
    cluster = relationship("NewsCluster", back_populates="posts")
    alerts = relationship("Alert", back_populates="post", cascade="all, delete-orphan")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    alert_type = Column(String(50), nullable=False)  # 'similar' | 'reactions' | 'trend' | 'important'
    reason = Column(Text, nullable=False)
    user_relevance_score = Column(Float, nullable=True)
    is_sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="alerts")
    post = relationship("Post", back_populates="alerts")


class NewsCluster(Base):
    __tablename__ = "news_clusters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_hash = Column(String(64), nullable=False, unique=True, index=True)
    canonical_text = Column(Text, nullable=False)
    canonical_summary = Column(Text, nullable=False)
    tags = Column(String(500), default="", nullable=False)  # comma-separated hashtags
    analogs = Column(Text, default="", nullable=False)  # comma-separated competitor analogs
    action_item = Column(Text, nullable=True)
    news_kind = Column(
        String(20),
        default="misc",
        nullable=False,
    )  # product | trend | research | tech_update | industry_report | misc
    implementable_by_small_team = Column(Boolean, default=False, nullable=False)
    infra_barrier = Column(String(10), default="high", nullable=False)  # low | medium | high
    product_score = Column(Float, default=0.0, nullable=False)
    priority = Column(String(10), default="low", nullable=False)  # high | medium | low
    is_alert_worthy = Column(Boolean, default=False, nullable=False)
    embedding = Column(Vector(384), nullable=True)
    is_ai_relevant = Column(Boolean, default=True, nullable=False)
    mention_count = Column(Integer, default=1, nullable=False)
    source_ids = Column(Text, default="", nullable=False)  # comma-separated source IDs
    coreai_score = Column(Float, default=0.0, nullable=False)
    coreai_reason = Column(Text, nullable=True)
    alert_sent_at = Column(DateTime, nullable=True)
    popularity_notified_mentions = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    posts = relationship("Post", back_populates="cluster")


class UserNewsFeedback(Base):
    __tablename__ = "user_news_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_id = Column(Integer, ForeignKey("news_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    vote = Column(Integer, nullable=False)  # 1 like, -1 dislike
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "cluster_id", name="uq_user_cluster_feedback"),
    )
