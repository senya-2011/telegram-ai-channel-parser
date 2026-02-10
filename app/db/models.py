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

    user = relationship("User", back_populates="settings")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)  # 'telegram' or 'web'
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
    external_id = Column(String(500), nullable=True)  # message_id or article URL
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    embedding = Column(Vector(384), nullable=True)  # all-MiniLM-L6-v2 outputs 384-dim
    reactions_count = Column(Integer, default=0, nullable=False)
    reactions_ratio = Column(Float, nullable=True)  # ratio vs avg for channel
    published_at = Column(DateTime, nullable=True)
    parsed_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_external_id"),
        Index("ix_post_published_at", "published_at"),
    )

    source = relationship("Source", back_populates="posts")
    alerts = relationship("Alert", back_populates="post", cascade="all, delete-orphan")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    alert_type = Column(String(50), nullable=False)  # 'similar' or 'reactions'
    reason = Column(Text, nullable=False)
    is_sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="alerts")
    post = relationship("Post", back_populates="alerts")
