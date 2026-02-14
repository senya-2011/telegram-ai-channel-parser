import datetime
from typing import Optional, Sequence

import bcrypt
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alert,
    NewsCluster,
    Post,
    Source,
    User,
    UserNewsFeedback,
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
    include_tech_updates: Optional[bool] = None,
    include_industry_reports: Optional[bool] = None,
    user_prompt: Optional[str] = None,
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
    if include_tech_updates is not None:
        user_settings.include_tech_updates = include_tech_updates
    if include_industry_reports is not None:
        user_settings.include_industry_reports = include_industry_reports
    if user_prompt is not None:
        user_settings.user_prompt = user_prompt
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
    commit: bool = True,
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
    if commit:
        await session.commit()
    else:
        await session.flush()
    return post


async def update_post_analysis(
    session: AsyncSession,
    post_id: int,
    summary: Optional[str] = None,
    embedding: Optional[list] = None,
    reactions_ratio: Optional[float] = None,
    normalized_hash: Optional[str] = None,
    is_ai_relevant: Optional[bool] = None,
    cluster_id: Optional[int] = None,
    commit: bool = True,
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
    if normalized_hash is not None:
        post.normalized_hash = normalized_hash
    if is_ai_relevant is not None:
        post.is_ai_relevant = is_ai_relevant
    if cluster_id is not None:
        post.cluster_id = cluster_id
    if commit:
        await session.commit()


async def get_unprocessed_posts(session: AsyncSession, limit: int = 50) -> Sequence[Post]:
    result = await session.execute(
        select(Post).where(Post.summary.is_(None)).order_by(Post.parsed_at.asc()).limit(limit)
    )
    return result.scalars().all()


async def delete_old_orphan_posts(
    session: AsyncSession,
    older_than_days: int = 7,
    limit: int = 500,
) -> int:
    """
    Удаляет посты, которые не вошли ни в один кластер (не продукт/технология/анализ)
    и старше older_than_days. Ограничение limit за один вызов, чтобы не блокировать БД.
    Возвращает количество удалённых строк.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=older_than_days)
    subq = (
        select(Post.id)
        .where(Post.cluster_id.is_(None), Post.parsed_at < cutoff)
        .order_by(Post.parsed_at.asc())
        .limit(limit)
    )
    result = await session.execute(subq)
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0
    await session.execute(delete(Post).where(Post.id.in_(ids)))
    await session.commit()
    return len(ids)


async def get_recent_post_by_hash(
    session: AsyncSession,
    normalized_hash: str,
    hours: int = 72,
) -> Optional[Post]:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    result = await session.execute(
        select(Post)
        .where(
            Post.normalized_hash == normalized_hash,
            Post.summary.isnot(None),
            Post.parsed_at >= cutoff,
        )
        .order_by(Post.parsed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_existing_external_ids(
    session: AsyncSession,
    source_id: int,
    external_ids: list[str],
) -> set[str]:
    if not external_ids:
        return set()
    result = await session.execute(
        select(Post.external_id)
        .where(Post.source_id == source_id, Post.external_id.in_(external_ids))
    )
    return {row[0] for row in result.all() if row[0]}


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


async def get_sources_by_ids(session: AsyncSession, source_ids: list[int]) -> dict[int, Source]:
    if not source_ids:
        return {}
    result = await session.execute(select(Source).where(Source.id.in_(source_ids)))
    items = result.scalars().all()
    return {item.id: item for item in items}


# ──────────────────────── News Clusters ────────────────────────

def _merge_source_ids(existing: str, source_id: int) -> str:
    source_set = {part for part in existing.split(",") if part}
    source_set.add(str(source_id))
    return ",".join(sorted(source_set, key=int))


def _merge_tags(existing: str, incoming: list[str] | None) -> str:
    tags = {tag.strip() for tag in (existing or "").split(",") if tag.strip()}
    if incoming:
        tags.update(tag.strip() for tag in incoming if tag and tag.strip())
    return ",".join(sorted(tags))


def _merge_analogs(existing: str, incoming: list[str] | None) -> str:
    analogs = {item.strip() for item in (existing or "").split(",") if item.strip()}
    if incoming:
        analogs.update(item.strip() for item in incoming if item and item.strip())
    return ",".join(sorted(analogs))


def _priority_rank(priority: str) -> int:
    mapping = {"low": 1, "medium": 2, "high": 3}
    return mapping.get((priority or "low").lower(), 1)


async def get_cluster_by_hash(session: AsyncSession, canonical_hash: str) -> Optional[NewsCluster]:
    result = await session.execute(
        select(NewsCluster).where(NewsCluster.canonical_hash == canonical_hash)
    )
    return result.scalar_one_or_none()


async def get_cluster_by_id(session: AsyncSession, cluster_id: int) -> Optional[NewsCluster]:
    result = await session.execute(select(NewsCluster).where(NewsCluster.id == cluster_id))
    return result.scalar_one_or_none()


async def create_news_cluster(
    session: AsyncSession,
    canonical_hash: str,
    canonical_text: str,
    canonical_summary: str,
    embedding: Optional[list],
    source_id: int,
    is_ai_relevant: bool = True,
    coreai_score: float = 0.0,
    coreai_reason: str = "",
    tags: list[str] | None = None,
    news_kind: str = "misc",
    implementable_by_small_team: bool = False,
    infra_barrier: str = "high",
    product_score: float = 0.0,
    priority: str = "low",
    is_alert_worthy: bool = False,
    analogs: list[str] | None = None,
    action_item: str = "",
) -> NewsCluster:
    cluster = NewsCluster(
        canonical_hash=canonical_hash,
        canonical_text=canonical_text,
        canonical_summary=canonical_summary,
        embedding=embedding,
        is_ai_relevant=is_ai_relevant,
        mention_count=1,
        source_ids=str(source_id),
        coreai_score=coreai_score,
        coreai_reason=coreai_reason,
        tags=_merge_tags("", tags),
        analogs=_merge_analogs("", analogs),
        action_item=action_item or None,
        news_kind=news_kind,
        implementable_by_small_team=implementable_by_small_team,
        infra_barrier=infra_barrier,
        product_score=product_score,
        priority=priority,
        is_alert_worthy=is_alert_worthy,
    )
    session.add(cluster)
    try:
        await session.commit()
        return cluster
    except IntegrityError:
        await session.rollback()
        existing = await get_cluster_by_hash(session, canonical_hash)
        if existing is None:
            raise
        return existing


async def attach_post_to_cluster(
    session: AsyncSession,
    post: Post,
    cluster: NewsCluster,
    normalized_hash: Optional[str] = None,
    is_ai_relevant: Optional[bool] = None,
    tags: list[str] | None = None,
    news_kind: Optional[str] = None,
    implementable_by_small_team: Optional[bool] = None,
    infra_barrier: Optional[str] = None,
    product_score: Optional[float] = None,
    priority: Optional[str] = None,
    is_alert_worthy: Optional[bool] = None,
    analogs: list[str] | None = None,
    action_item: Optional[str] = None,
    commit: bool = True,
) -> None:
    post.cluster_id = cluster.id
    post.summary = cluster.canonical_summary
    if cluster.embedding is not None:
        post.embedding = cluster.embedding
    if normalized_hash is not None:
        post.normalized_hash = normalized_hash
    if is_ai_relevant is not None:
        post.is_ai_relevant = is_ai_relevant

    cluster.mention_count = (cluster.mention_count or 0) + 1
    cluster.source_ids = _merge_source_ids(cluster.source_ids or "", post.source_id)
    cluster.tags = _merge_tags(cluster.tags or "", tags)
    cluster.analogs = _merge_analogs(cluster.analogs or "", analogs)
    if action_item and _priority_rank(priority or cluster.priority) >= _priority_rank(cluster.priority):
        cluster.action_item = action_item
    if product_score is not None:
        cluster.product_score = max(float(cluster.product_score or 0.0), float(product_score))
    if news_kind:
        if cluster.news_kind not in {"product", "tech_update", "industry_report"} or news_kind in {"product", "tech_update", "industry_report"}:
            cluster.news_kind = news_kind
    if implementable_by_small_team is not None:
        cluster.implementable_by_small_team = bool(
            cluster.implementable_by_small_team or implementable_by_small_team
        )
    if infra_barrier in {"low", "medium", "high"}:
        rank = {"low": 1, "medium": 2, "high": 3}
        current = (cluster.infra_barrier or "high").lower()
        if rank.get(infra_barrier, 3) < rank.get(current, 3):
            cluster.infra_barrier = infra_barrier
    if priority and _priority_rank(priority) > _priority_rank(cluster.priority):
        cluster.priority = priority
    if is_alert_worthy is not None:
        cluster.is_alert_worthy = bool(cluster.is_alert_worthy or is_alert_worthy)
    cluster.updated_at = datetime.datetime.utcnow()
    if commit:
        await session.commit()


async def find_similar_clusters(
    session: AsyncSession,
    embedding: list,
    threshold: float = 0.84,
    hours: int = 96,
    limit: int = 5,
) -> list[NewsCluster]:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    result = await session.execute(
        select(NewsCluster)
        .where(
            NewsCluster.embedding.isnot(None),
            NewsCluster.created_at >= cutoff,
            NewsCluster.is_ai_relevant == True,
        )
        .order_by(NewsCluster.embedding.cosine_distance(embedding))
        .limit(limit)
    )
    return result.scalars().all()


async def get_pending_clusters_for_alerts(
    session: AsyncSession,
    min_mentions: int = 2,
    limit: int = 20,
) -> Sequence[NewsCluster]:
    result = await session.execute(
        select(NewsCluster)
        .where(
            NewsCluster.is_ai_relevant == True,
            NewsCluster.mention_count >= min_mentions,
            NewsCluster.alert_sent_at.is_(None),
        )
        .order_by(NewsCluster.updated_at.asc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_pending_important_clusters_for_alerts(
    session: AsyncSession,
    min_core_score: float,
    min_product_score: float,
    limit: int = 20,
) -> Sequence[NewsCluster]:
    result = await session.execute(
        select(NewsCluster)
        .where(
            NewsCluster.is_ai_relevant == True,
            NewsCluster.alert_sent_at.is_(None),
            NewsCluster.is_alert_worthy == True,
            (
                (NewsCluster.coreai_score >= min_core_score)
                | (
                    (NewsCluster.news_kind.in_(["product", "tech_update"]))
                    & (NewsCluster.product_score >= min_product_score)
                    & (NewsCluster.priority.in_(["high", "medium"]))
                )
            ),
        )
        .order_by(NewsCluster.coreai_score.desc(), NewsCluster.product_score.desc(), NewsCluster.updated_at.asc())
        .limit(limit)
    )
    return result.scalars().all()


async def mark_cluster_alert_sent(session: AsyncSession, cluster_id: int) -> None:
    result = await session.execute(select(NewsCluster).where(NewsCluster.id == cluster_id))
    cluster = result.scalar_one_or_none()
    if cluster is None:
        return
    cluster.alert_sent_at = datetime.datetime.utcnow()
    cluster.popularity_notified_mentions = max(
        cluster.popularity_notified_mentions or 0,
        cluster.mention_count or 0,
    )
    await session.commit()


async def get_posts_for_cluster(session: AsyncSession, cluster_id: int, limit: int = 20) -> Sequence[Post]:
    result = await session.execute(
        select(Post)
        .where(Post.cluster_id == cluster_id)
        .order_by(Post.published_at.desc().nullslast(), Post.parsed_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_clusters_by_ids(session: AsyncSession, cluster_ids: list[int]) -> dict[int, NewsCluster]:
    if not cluster_ids:
        return {}
    result = await session.execute(select(NewsCluster).where(NewsCluster.id.in_(cluster_ids)))
    clusters = result.scalars().all()
    return {cluster.id: cluster for cluster in clusters}


def _next_popularity_threshold(mentions: int) -> int | None:
    # Sparse thresholds to avoid spam and only notify on meaningful growth.
    thresholds = (3, 5, 8, 13, 21, 34, 55, 89)
    for value in thresholds:
        if mentions >= value:
            candidate = value
    return candidate if "candidate" in locals() else None


async def get_clusters_for_popularity_updates(
    session: AsyncSession,
    limit: int = 20,
) -> Sequence[NewsCluster]:
    result = await session.execute(
        select(NewsCluster)
        .where(
            NewsCluster.alert_sent_at.isnot(None),
            NewsCluster.mention_count >= 3,
        )
        .order_by(NewsCluster.updated_at.asc())
        .limit(limit * 3)
    )
    clusters = result.scalars().all()

    eligible: list[NewsCluster] = []
    for cluster in clusters:
        target = _next_popularity_threshold(cluster.mention_count or 0)
        if target is None:
            continue
        if (cluster.popularity_notified_mentions or 0) >= target:
            continue
        eligible.append(cluster)
        if len(eligible) >= limit:
            break
    return eligible


async def mark_cluster_popularity_notified(
    session: AsyncSession,
    cluster_id: int,
    mentions: int,
) -> None:
    result = await session.execute(select(NewsCluster).where(NewsCluster.id == cluster_id))
    cluster = result.scalar_one_or_none()
    if cluster is None:
        return
    cluster.popularity_notified_mentions = max(cluster.popularity_notified_mentions or 0, mentions)
    cluster.updated_at = datetime.datetime.utcnow()
    await session.commit()


# ──────────────────────── Alerts ────────────────────────

async def create_alert(
    session: AsyncSession,
    user_id: int,
    post_id: int,
    alert_type: str,
    reason: str,
    user_relevance_score: Optional[float] = None,
) -> Alert:
    alert = Alert(
        user_id=user_id,
        post_id=post_id,
        alert_type=alert_type,
        reason=reason,
        user_relevance_score=user_relevance_score,
    )
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


async def get_subscribers_for_sources(session: AsyncSession, source_ids: list[int]) -> dict[int, list[User]]:
    if not source_ids:
        return {}
    result = await session.execute(
        select(UserSource.source_id, User)
        .join(User, User.id == UserSource.user_id)
        .where(UserSource.source_id.in_(source_ids))
    )
    mapping: dict[int, list[User]] = {sid: [] for sid in source_ids}
    for source_id, user in result.all():
        mapping.setdefault(source_id, []).append(user)
    return mapping


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


async def upsert_user_feedback(
    session: AsyncSession,
    user_id: int,
    cluster_id: int,
    vote: int,
) -> UserNewsFeedback:
    vote = 1 if vote >= 1 else -1
    result = await session.execute(
        select(UserNewsFeedback).where(
            UserNewsFeedback.user_id == user_id,
            UserNewsFeedback.cluster_id == cluster_id,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.vote = vote
        await session.commit()
        return row
    row = UserNewsFeedback(user_id=user_id, cluster_id=cluster_id, vote=vote)
    session.add(row)
    await session.commit()
    return row


async def get_user_kind_feedback_bias(session: AsyncSession, user_id: int) -> dict[str, float]:
    result = await session.execute(
        select(NewsCluster.news_kind, func.avg(UserNewsFeedback.vote))
        .join(NewsCluster, NewsCluster.id == UserNewsFeedback.cluster_id)
        .where(UserNewsFeedback.user_id == user_id)
        .group_by(NewsCluster.news_kind)
    )
    return {kind: float(score) for kind, score in result.all()}


async def get_user_disliked_clusters(session: AsyncSession, user_id: int) -> Sequence[NewsCluster]:
    """Кластеры, которые пользователь отметил «Мимо». Для фильтра «не показывать похожее»."""
    result = await session.execute(
        select(NewsCluster)
        .join(UserNewsFeedback, UserNewsFeedback.cluster_id == NewsCluster.id)
        .where(
            UserNewsFeedback.user_id == user_id,
            UserNewsFeedback.vote == -1,
        )
    )
    return result.scalars().unique().all()
