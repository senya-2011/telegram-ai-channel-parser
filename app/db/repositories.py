import datetime
from typing import Optional, Sequence

import bcrypt
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alert,
    Post,
    Source,
    User,
    UserSettings,
    UserSource,
    UserTelegramLink,
)


# ──────────────────────── Users ────────────────────────

async def create_user(session: AsyncSession, username: str, password: str) -> User:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=password_hash)
    session.add(user)
    await session.flush()
    # Create default settings
    user_settings = UserSettings(user_id=user.id, digest_time="20:00", timezone="Europe/Moscow")
    session.add(user_settings)
    await session.commit()
    return user


async def authenticate_user(session: AsyncSession, username: str, password: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return user
    return None


async def get_user_by_telegram_id(session: AsyncSession, telegram_user_id: int) -> Optional[User]:
    result = await session.execute(
        select(User)
        .join(UserTelegramLink, User.id == UserTelegramLink.user_id)
        .where(UserTelegramLink.telegram_user_id == telegram_user_id)
    )
    return result.scalar_one_or_none()


async def link_telegram_account(session: AsyncSession, user_id: int, telegram_user_id: int) -> UserTelegramLink:
    link = UserTelegramLink(user_id=user_id, telegram_user_id=telegram_user_id)
    session.add(link)
    await session.commit()
    return link


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


# ──────────────────────── User Settings ────────────────────────

async def get_user_settings(session: AsyncSession, user_id: int) -> Optional[UserSettings]:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    return result.scalar_one_or_none()


async def update_user_settings(
    session: AsyncSession,
    user_id: int,
    digest_time: Optional[str] = None,
    timezone: Optional[str] = None,
) -> UserSettings:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    user_settings = result.scalar_one_or_none()
    if not user_settings:
        user_settings = UserSettings(user_id=user_id)
        session.add(user_settings)
    if digest_time is not None:
        user_settings.digest_time = digest_time
    if timezone is not None:
        user_settings.timezone = timezone
    await session.commit()
    return user_settings


# ──────────────────────── Sources ────────────────────────

async def get_or_create_source(
    session: AsyncSession,
    source_type: str,
    identifier: str,
    title: Optional[str] = None,
    is_default: bool = False,
) -> Source:
    result = await session.execute(
        select(Source).where(Source.type == source_type, Source.identifier == identifier)
    )
    source = result.scalar_one_or_none()
    if source:
        return source
    source = Source(type=source_type, identifier=identifier, title=title, is_default=is_default)
    session.add(source)
    await session.commit()
    return source


async def subscribe_user_to_source(session: AsyncSession, user_id: int, source_id: int) -> bool:
    result = await session.execute(
        select(UserSource).where(UserSource.user_id == user_id, UserSource.source_id == source_id)
    )
    if result.scalar_one_or_none():
        return False  # Already subscribed
    session.add(UserSource(user_id=user_id, source_id=source_id))
    await session.commit()
    return True


async def unsubscribe_user_from_source(session: AsyncSession, user_id: int, source_id: int) -> bool:
    result = await session.execute(
        delete(UserSource).where(UserSource.user_id == user_id, UserSource.source_id == source_id)
    )
    await session.commit()
    return result.rowcount > 0


async def get_user_sources(
    session: AsyncSession, user_id: int, source_type: Optional[str] = None
) -> Sequence[Source]:
    query = (
        select(Source)
        .join(UserSource, Source.id == UserSource.source_id)
        .where(UserSource.user_id == user_id)
    )
    if source_type:
        query = query.where(Source.type == source_type)
    query = query.order_by(Source.title)
    result = await session.execute(query)
    return result.scalars().all()


async def get_all_sources(session: AsyncSession, source_type: Optional[str] = None) -> Sequence[Source]:
    query = select(Source)
    if source_type:
        query = query.where(Source.type == source_type)
    result = await session.execute(query)
    return result.scalars().all()


async def get_default_sources(session: AsyncSession) -> Sequence[Source]:
    result = await session.execute(select(Source).where(Source.is_default == True))
    return result.scalars().all()


async def subscribe_user_to_defaults(session: AsyncSession, user_id: int) -> None:
    defaults = await get_default_sources(session)
    for source in defaults:
        await subscribe_user_to_source(session, user_id, source.id)


# ──────────────────────── Posts ────────────────────────

async def create_post(
    session: AsyncSession,
    source_id: int,
    external_id: str,
    content: str,
    reactions_count: int = 0,
    published_at: Optional[datetime.datetime] = None,
) -> Optional[Post]:
    # Check duplicate
    result = await session.execute(
        select(Post).where(Post.source_id == source_id, Post.external_id == external_id)
    )
    if result.scalar_one_or_none():
        return None  # Already exists
    post = Post(
        source_id=source_id,
        external_id=external_id,
        content=content,
        reactions_count=reactions_count,
        published_at=published_at,
    )
    session.add(post)
    await session.commit()
    return post


async def update_post_analysis(
    session: AsyncSession,
    post_id: int,
    summary: Optional[str] = None,
    embedding: Optional[list] = None,
    reactions_ratio: Optional[float] = None,
) -> None:
    result = await session.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        return
    if summary is not None:
        post.summary = summary
    if embedding is not None:
        post.embedding = embedding
    if reactions_ratio is not None:
        post.reactions_ratio = reactions_ratio
    await session.commit()


async def get_unprocessed_posts(session: AsyncSession, limit: int = 50) -> Sequence[Post]:
    result = await session.execute(
        select(Post).where(Post.summary.is_(None)).order_by(Post.parsed_at.asc()).limit(limit)
    )
    return result.scalars().all()


async def find_similar_posts(
    session: AsyncSession, embedding: list, threshold: float = 0.82, hours: int = 48, exclude_post_id: int = 0
) -> Sequence[Post]:
    """Find posts with cosine similarity above threshold within the last N hours."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    result = await session.execute(
        select(Post)
        .where(
            Post.embedding.isnot(None),
            Post.published_at >= cutoff,
            Post.id != exclude_post_id,
        )
        .order_by(Post.embedding.cosine_distance(embedding))
        .limit(10)
    )
    posts = result.scalars().all()
    # Filter by threshold (cosine_distance = 1 - similarity)
    return [p for p in posts if p.embedding is not None]


async def get_avg_reactions_for_source(session: AsyncSession, source_id: int, days: int = 7) -> float:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    result = await session.execute(
        select(func.avg(Post.reactions_count))
        .where(Post.source_id == source_id, Post.published_at >= cutoff)
    )
    avg = result.scalar_one_or_none()
    return float(avg) if avg else 0.0


async def get_posts_for_digest(
    session: AsyncSession, source_ids: list[int], hours: int = 24, limit: int = 20
) -> Sequence[Post]:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    result = await session.execute(
        select(Post)
        .where(
            Post.source_id.in_(source_ids),
            Post.published_at >= cutoff,
            Post.summary.isnot(None),
        )
        .order_by(Post.reactions_count.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_source_by_id(session: AsyncSession, source_id: int) -> Optional[Source]:
    result = await session.execute(select(Source).where(Source.id == source_id))
    return result.scalar_one_or_none()


# ──────────────────────── Alerts ────────────────────────

async def create_alert(
    session: AsyncSession,
    user_id: int,
    post_id: int,
    alert_type: str,
    reason: str,
) -> Alert:
    alert = Alert(user_id=user_id, post_id=post_id, alert_type=alert_type, reason=reason)
    session.add(alert)
    await session.commit()
    return alert


async def get_unsent_alerts(session: AsyncSession, user_id: Optional[int] = None) -> Sequence[Alert]:
    query = select(Alert).where(Alert.is_sent == False)
    if user_id:
        query = query.where(Alert.user_id == user_id)
    query = query.order_by(Alert.created_at.asc())
    result = await session.execute(query)
    return result.scalars().all()


async def mark_alert_sent(session: AsyncSession, alert_id: int) -> None:
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert:
        alert.is_sent = True
        await session.commit()


async def get_subscribers_for_source(session: AsyncSession, source_id: int) -> Sequence[User]:
    result = await session.execute(
        select(User)
        .join(UserSource, User.id == UserSource.user_id)
        .where(UserSource.source_id == source_id)
    )
    return result.scalars().all()


async def get_telegram_ids_for_user(session: AsyncSession, user_id: int) -> list[int]:
    result = await session.execute(
        select(UserTelegramLink.telegram_user_id).where(UserTelegramLink.user_id == user_id)
    )
    return [row[0] for row in result.all()]


async def get_all_users_with_settings(session: AsyncSession) -> Sequence[User]:
    result = await session.execute(
        select(User).join(UserSettings, User.id == UserSettings.user_id)
    )
    return result.scalars().all()
