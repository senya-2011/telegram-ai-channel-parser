import hashlib
import logging
import re

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import NewsCluster, Post
from app.db.repositories import (
    attach_post_to_cluster,
    create_alert,
    create_news_cluster,
    find_similar_clusters,
    get_avg_reactions_for_source,
    get_cluster_by_id,
    get_pending_clusters_for_alerts,
    get_posts_for_cluster,
    get_recent_post_by_hash,
    get_source_by_id,
    get_sources_by_ids,
    get_subscribers_for_source,
    get_subscribers_for_sources,
    get_telegram_ids_for_user,
    get_unprocessed_posts,
    get_clusters_for_popularity_updates,
    mark_cluster_popularity_notified,
    mark_cluster_alert_sent,
)
from app.services.embedding import cosine_similarity, generate_embedding
from app.services.llm_client import analyze_post, check_similarity

logger = logging.getLogger(__name__)

_NOISE_PATTERNS = (
    r"https?://\S+",
    r"@\w+",
    r"#\w+",
    r"[^\w\s]",
)

_AI_PREFILTER = (
    "ai", "ml", "llm", "gpt", "openai", "deepseek", "anthropic", "gemini", "claude",
    "нейросет", "искусствен", "машинн обучен", "ии", "модель", "агент",
)


def _normalize_text(text: str) -> str:
    cleaned = text.lower()
    for pattern in _NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:4000]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _escape_md_url(url: str) -> str:
    return url.replace("(", "%28").replace(")", "%29")


def _extract_first_url(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip(").,]") if match else ""


def _soft_limit(text: str, max_len: int = 420) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0].strip()
    return (cut or text[:max_len]).rstrip(".,;:") + "..."


def _get_post_link(source, post) -> str:
    if source and source.type == "telegram":
        channel = source.identifier.lstrip("@")
        return f"https://t.me/{channel}/{post.external_id}"
    if post.external_id and post.external_id.startswith("http"):
        return _escape_md_url(post.external_id)
    return ""


def _quick_prefilter(text: str) -> bool:
    text_lower = text.lower()
    return any(token in text_lower for token in _AI_PREFILTER)


async def process_new_posts(session: AsyncSession, bot: Bot):
    unprocessed = await get_unprocessed_posts(session, limit=40)
    if not unprocessed:
        logger.debug("No unprocessed posts found")
        return

    logger.info(f"Processing {len(unprocessed)} new posts...")
    relevant_posts: list[Post] = []
    avg_reactions_cache: dict[int, float] = {}

    for post in unprocessed:
        try:
            is_relevant = await _analyze_and_cluster_post(session, post, avg_reactions_cache)
            if is_relevant:
                relevant_posts.append(post)
        except Exception as e:
            logger.error(f"Error analyzing post {post.id}: {e}")

    await _send_cluster_alerts(session, bot)
    await _send_cluster_popularity_updates(session, bot)

    for post in relevant_posts:
        try:
            if post.reactions_ratio and post.reactions_ratio >= settings.reactions_multiplier:
                await _send_reactions_alert(session, bot, post, post.reactions_ratio)
        except Exception as e:
            logger.error(f"Error sending reaction alert for post {post.id}: {e}")


async def _analyze_and_cluster_post(
    session: AsyncSession,
    post: Post,
    avg_reactions_cache: dict[int, float],
) -> bool:
    normalized = _normalize_text(post.content)
    normalized_hash = _hash_text(normalized)
    reactions_ratio = await _calc_reactions_ratio(session, post, avg_reactions_cache)

    existing = await get_recent_post_by_hash(session, normalized_hash=normalized_hash, hours=96)
    if existing:
        post.summary = existing.summary
        post.embedding = existing.embedding
        post.is_ai_relevant = existing.is_ai_relevant
        post.normalized_hash = normalized_hash
        post.reactions_ratio = reactions_ratio
        if existing.cluster_id:
            cluster = await get_cluster_by_id(session, existing.cluster_id)
            if cluster:
                await attach_post_to_cluster(
                    session,
                    post=post,
                    cluster=cluster,
                    normalized_hash=normalized_hash,
                    is_ai_relevant=bool(existing.is_ai_relevant),
                    commit=False,
                )
        await session.commit()
        return bool(post.is_ai_relevant)

    if not _quick_prefilter(post.content):
        post.summary = post.content[:240] + "..." if len(post.content) > 240 else post.content
        post.normalized_hash = normalized_hash
        post.is_ai_relevant = False
        post.reactions_ratio = reactions_ratio
        await session.commit()
        return False

    analysis = await analyze_post(post.content)
    summary = analysis["summary"]
    is_relevant = bool(analysis["is_relevant"])
    coreai_score = float(analysis.get("coreai_score", 0.0))
    coreai_reason = analysis.get("coreai_reason", "")
    tags = analysis.get("tags", [])

    post.summary = summary
    post.normalized_hash = normalized_hash
    post.is_ai_relevant = is_relevant
    post.reactions_ratio = reactions_ratio

    if not is_relevant:
        await session.commit()
        return False

    embedding = generate_embedding(summary or post.content)
    post.embedding = embedding

    matched_cluster = await _match_cluster(session, summary=summary, embedding=embedding)
    if matched_cluster:
        await attach_post_to_cluster(
            session,
            post=post,
            cluster=matched_cluster,
            normalized_hash=normalized_hash,
            is_ai_relevant=True,
            tags=tags,
            commit=False,
        )
        await session.commit()
        return True

    cluster = await create_news_cluster(
        session=session,
        canonical_hash=normalized_hash,
        canonical_text=post.content[:4000],
        canonical_summary=summary,
        embedding=embedding,
        source_id=post.source_id,
        is_ai_relevant=True,
        coreai_score=coreai_score,
        coreai_reason=coreai_reason,
        tags=tags,
    )
    post.cluster_id = cluster.id
    await session.commit()
    return True


async def _calc_reactions_ratio(
    session: AsyncSession,
    post: Post,
    avg_reactions_cache: dict[int, float],
) -> float | None:
    if post.reactions_count <= 0:
        return None
    if post.source_id not in avg_reactions_cache:
        avg_reactions_cache[post.source_id] = await get_avg_reactions_for_source(
            session, post.source_id, days=7
        )
    avg = avg_reactions_cache[post.source_id]
    if avg <= 0:
        return None
    return post.reactions_count / avg


async def _match_cluster(
    session: AsyncSession,
    summary: str,
    embedding: list | None,
) -> NewsCluster | None:
    if embedding is None:
        return None
    candidates = await find_similar_clusters(
        session,
        embedding=embedding,
        threshold=settings.similarity_threshold,
        hours=96,
        limit=5,
    )
    if not candidates:
        return None

    hard_threshold = min(0.97, settings.similarity_threshold + 0.06)
    soft_threshold = settings.similarity_threshold
    best_soft: NewsCluster | None = None
    best_soft_sim = 0.0

    for candidate in candidates:
        if candidate.embedding is None:
            continue
        sim = cosine_similarity(embedding, list(candidate.embedding))
        if sim >= hard_threshold:
            return candidate
        if sim >= soft_threshold and sim > best_soft_sim:
            best_soft = candidate
            best_soft_sim = sim

    if best_soft is None:
        return None

    result = await check_similarity(summary, best_soft.canonical_summary)
    if result["is_similar"]:
        return best_soft
    return None


async def _send_cluster_alerts(session: AsyncSession, bot: Bot) -> None:
    clusters = await get_pending_clusters_for_alerts(
        session,
        min_mentions=settings.cluster_min_mentions,
        limit=20,
    )
    for cluster in clusters:
        try:
            await _send_similarity_alert_for_cluster(session, bot, cluster)
            await mark_cluster_alert_sent(session, cluster.id)
        except Exception as e:
            logger.error(f"Error sending cluster alert {cluster.id}: {e}")


async def _send_cluster_popularity_updates(session: AsyncSession, bot: Bot) -> None:
    clusters = await get_clusters_for_popularity_updates(session, limit=20)
    for cluster in clusters:
        try:
            await _send_cluster_popularity_message(session, bot, cluster)
            await mark_cluster_popularity_notified(session, cluster.id, cluster.mention_count or 0)
        except Exception as e:
            logger.error(f"Error sending popularity update for cluster {cluster.id}: {e}")


async def _send_cluster_popularity_message(
    session: AsyncSession,
    bot: Bot,
    cluster: NewsCluster,
) -> None:
    posts = await get_posts_for_cluster(session, cluster.id, limit=30)
    if not posts:
        return

    source_ids = sorted({p.source_id for p in posts})
    sources_map = await get_sources_by_ids(session, source_ids)
    subscribers_map = await get_subscribers_for_sources(session, source_ids)
    representative_post = posts[0]

    links_lines: list[str] = []
    seen_sources: set[int] = set()
    for post in posts:
        if post.source_id in seen_sources:
            continue
        source = sources_map.get(post.source_id)
        title = source.title or source.identifier if source else f"source:{post.source_id}"
        post_link = _get_post_link(source, post)
        if post_link:
            links_lines.append(f'• <a href="{post_link}">{title}</a>')
        else:
            fallback_url = _extract_first_url(post.content or "")
            if fallback_url:
                links_lines.append(f'• <a href="{fallback_url}">{title}</a>')
            else:
                links_lines.append(f"• {title}")
        seen_sources.add(post.source_id)

    reason = (
        "📈 <b>Обновление по новости:</b> тема набирает популярность\n\n"
        f"📰 <b>Суть:</b> {cluster.canonical_summary[:220]}\n"
        f"🏷 <b>Теги:</b> {' '.join(tag for tag in (cluster.tags or '').split(',') if tag) or '#AIТехнологии'}\n"
        f"📡 <b>Уже источников:</b> {cluster.mention_count}\n"
        f"🔁 <b>Рост:</b> новость продолжает появляться в новых каналах/сайтах\n\n"
        f"{chr(10).join(links_lines)}"
    )

    topic = cluster.canonical_summary[:100]
    users = []
    seen_user_ids = set()
    for source_id in source_ids:
        for user in subscribers_map.get(source_id, []):
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)
            users.append(user)

    for user in users:
        await create_alert(session, user.id, representative_post.id, "trend", reason)
        await _send_alert_to_user(bot, session, user.id, reason, topic=topic)


async def _send_similarity_alert_for_cluster(
    session: AsyncSession,
    bot: Bot,
    cluster: NewsCluster,
) -> None:
    posts = await get_posts_for_cluster(session, cluster.id, limit=30)
    if not posts:
        return

    source_ids = sorted({p.source_id for p in posts})
    sources_map = await get_sources_by_ids(session, source_ids)
    subscribers_map = await get_subscribers_for_sources(session, source_ids)

    seen_sources: set[int] = set()
    links_lines: list[str] = []
    source_titles: list[str] = []
    representative_post = posts[0]

    for post in posts:
        if post.source_id in seen_sources:
            continue
        source = sources_map.get(post.source_id)
        title = source.title or source.identifier if source else f"source:{post.source_id}"
        source_titles.append(title)
        post_link = _get_post_link(source, post)
        if post_link:
            links_lines.append(f'🔗 <a href="{post_link}">{title}</a>')
        else:
            fallback_url = _extract_first_url(post.content or "")
            if fallback_url:
                links_lines.append(f'🔗 <a href="{fallback_url}">{title}</a>')
            else:
                links_lines.append(f"🔗 {title}")
        seen_sources.add(post.source_id)

    topic = cluster.canonical_summary[:120]
    coreai_line = ""
    if cluster.coreai_score >= settings.coreai_alert_threshold:
        core_reason = _soft_limit(cluster.coreai_reason, max_len=420)
        coreai_line = f"\n🏷 <b>CoreAI:</b> {cluster.coreai_score:.2f} - {core_reason}\n"
    tags_text = " ".join(tag for tag in (cluster.tags or "").split(",") if tag) or "#AIТехнологии"

    reason = (
        "🔔 Похожая новость подтверждена в нескольких источниках\n\n"
        f"📰 <b>Суть:</b> {cluster.canonical_summary[:260]}\n"
        f"🏷 <b>Теги:</b> {tags_text}\n"
        f"📡 <b>Источников:</b> {cluster.mention_count}\n"
        f"{coreai_line}"
        f"\n{chr(10).join(links_lines)}"
    )

    all_users = []
    seen_user_ids = set()
    for source_id in source_ids:
        for user in subscribers_map.get(source_id, []):
            if user.id in seen_user_ids:
                continue
            seen_user_ids.add(user.id)
            all_users.append(user)

    for user in all_users:
        await create_alert(session, user.id, representative_post.id, "similar", reason)
        await _send_alert_to_user(bot, session, user.id, reason, topic=topic)


async def _send_reactions_alert(
    session: AsyncSession,
    bot: Bot,
    post: Post,
    reactions_ratio: float,
):
    source = await get_source_by_id(session, post.source_id)
    source_title = source.title or source.identifier if source else "Неизвестный"
    post_link = _get_post_link(source, post)
    link_text = f'\n🔗 <a href="{post_link}">Открыть оригинал</a>' if post_link else ""
    tags_text = "#AIТехнологии"
    if post.cluster_id:
        cluster = await get_cluster_by_id(session, post.cluster_id)
        if cluster and cluster.tags:
            tags_text = " ".join(tag for tag in cluster.tags.split(",") if tag)

    reason = (
        "🔥 Пост с аномально высокой активностью\n\n"
        f"📰 <b>Новость:</b> {(post.summary or post.content)[:220]}\n\n"
        f"🏷 <b>Теги:</b> {tags_text}\n"
        f"📡 <b>Канал:</b> {source_title}\n"
        f"👍 <b>Реакций:</b> {post.reactions_count} "
        f"(в {reactions_ratio:.1f}x выше среднего){link_text}"
    )

    topic = (post.summary or post.content)[:100]
    subscribers = await get_subscribers_for_source(session, post.source_id)
    for user in subscribers:
        await create_alert(session, user.id, post.id, "reactions", reason)
        await _send_alert_to_user(bot, session, user.id, reason, topic=topic)


async def _send_alert_to_user(bot: Bot, session: AsyncSession, user_id: int, text: str, topic: str = ""):
    from app.bot.keyboards import alert_keyboard

    keyboard = alert_keyboard(topic) if topic else None
    telegram_ids = await get_telegram_ids_for_user(session, user_id)
    for tg_id in telegram_ids:
        try:
            await bot.send_message(tg_id, text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            try:
                await bot.send_message(tg_id, text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Failed to send alert to tg_id={tg_id}: {e}")
