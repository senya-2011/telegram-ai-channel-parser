import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Post
from app.db.repositories import (
    create_alert,
    get_avg_reactions_for_source,
    get_source_by_id,
    get_subscribers_for_source,
    get_telegram_ids_for_user,
    get_unprocessed_posts,
    update_post_analysis,
)
from app.services.embedding import generate_embedding
from app.services.llm_client import summarize_post
from app.services.similarity import find_confirmed_similar_posts

logger = logging.getLogger(__name__)


def _escape_md_url(url: str) -> str:
    """Escape parentheses in URLs so Markdown links don't break."""
    return url.replace("(", "%28").replace(")", "%29")


def _get_post_link(source, post) -> str:
    """Generate a link to the original post/article."""
    if source and source.type == "telegram":
        channel = source.identifier.lstrip("@")
        return f"https://t.me/{channel}/{post.external_id}"
    elif post.external_id and post.external_id.startswith("http"):
        return _escape_md_url(post.external_id)
    return ""


async def process_new_posts(session: AsyncSession, bot: Bot):
    """
    Main processing pipeline for new posts:
    1. Generate summary (LLM)
    2. Generate embedding
    3. Check for similar posts (vector search + LLM confirmation)
    4. Check for reaction anomalies
    5. Send alerts
    """
    unprocessed = await get_unprocessed_posts(session, limit=30)

    if not unprocessed:
        logger.debug("No unprocessed posts found")
        return

    logger.info(f"Processing {len(unprocessed)} new posts...")

    for post in unprocessed:
        try:
            await _process_single_post(session, bot, post)
        except Exception as e:
            logger.error(f"Error processing post {post.id}: {e}")


async def _process_single_post(session: AsyncSession, bot: Bot, post: Post):
    """Process a single post through the full pipeline."""

    # Step 1: Generate summary
    summary = await summarize_post(post.content)
    if not summary:
        # If LLM fails, use truncated content as summary
        summary = post.content[:300] + "..." if len(post.content) > 300 else post.content

    # Step 2: Generate embedding
    text_for_embedding = summary or post.content
    embedding = generate_embedding(text_for_embedding)

    # Step 3: Check reaction anomaly
    reactions_ratio = None
    if post.reactions_count > 0:
        avg_reactions = await get_avg_reactions_for_source(session, post.source_id, days=7)
        if avg_reactions > 0:
            reactions_ratio = post.reactions_count / avg_reactions

    # Update post with analysis results
    await update_post_analysis(
        session,
        post_id=post.id,
        summary=summary,
        embedding=embedding,
        reactions_ratio=reactions_ratio,
    )

    # Refresh post data
    post.summary = summary
    post.embedding = embedding
    post.reactions_ratio = reactions_ratio

    # Step 4: Check for similar posts
    if embedding is not None:
        similar_posts = await find_confirmed_similar_posts(session, post)
        if similar_posts:
            await _send_similarity_alert(session, bot, post, similar_posts)

    # Step 5: Check reaction anomaly alert
    if reactions_ratio and reactions_ratio >= settings.reactions_multiplier:
        await _send_reactions_alert(session, bot, post, reactions_ratio)


async def _send_similarity_alert(
    session: AsyncSession, bot: Bot, post: Post, similar_posts: list[dict]
):
    """Create and send alerts for similar posts across channels."""
    source = await get_source_by_id(session, post.source_id)
    source_title = source.title or source.identifier if source else "Неизвестный"
    post_link = _get_post_link(source, post)

    similar_sources = [s["source_title"] for s in similar_posts]
    explanation = similar_posts[0]["explanation"] if similar_posts else ""

    # Build links for all similar posts
    links_text = ""
    if post_link:
        links_text += f"🔗 [{source_title}]({post_link})\n"
    for sp in similar_posts:
        sp_source = await get_source_by_id(session, sp["post"].source_id)
        sp_link = _get_post_link(sp_source, sp["post"])
        if sp_link:
            links_text += f"🔗 [{sp['source_title']}]({sp_link})\n"

    reason = (
        f"🔔 Похожая новость найдена в нескольких каналах!\n\n"
        f"📰 **Новость:** {post.summary[:200]}\n\n"
        f"📡 **Источники:** {source_title}, {', '.join(similar_sources)}\n\n"
        f"{links_text}\n"
        f"💡 **Анализ:** {explanation}"
    )

    # Get all subscribers for this source
    subscribers = await get_subscribers_for_source(session, post.source_id)

    # Also get subscribers of similar posts' sources
    seen_user_ids = set()
    for sub in subscribers:
        seen_user_ids.add(sub.id)

    for sim_post in similar_posts:
        sim_subscribers = await get_subscribers_for_source(session, sim_post["post"].source_id)
        for sub in sim_subscribers:
            if sub.id not in seen_user_ids:
                subscribers.append(sub)
                seen_user_ids.add(sub.id)

    for user in subscribers:
        alert = await create_alert(session, user.id, post.id, "similar", reason)
        await _send_alert_to_user(bot, session, user.id, reason)


async def _send_reactions_alert(
    session: AsyncSession, bot: Bot, post: Post, reactions_ratio: float
):
    """Create and send alerts for posts with abnormally high reactions."""
    source = await get_source_by_id(session, post.source_id)
    source_title = source.title or source.identifier if source else "Неизвестный"
    post_link = _get_post_link(source, post)
    link_text = f"\n🔗 [Открыть оригинал]({post_link})" if post_link else ""

    reason = (
        f"🔥 Пост с аномально высокой активностью!\n\n"
        f"📰 **Новость:** {post.summary[:200]}\n\n"
        f"📡 **Канал:** {source_title}\n"
        f"👍 **Реакций:** {post.reactions_count} "
        f"(в {reactions_ratio:.1f}x раз больше среднего)\n\n"
        f"💡 Этот пост вызвал значительно больше реакций, чем обычные посты в этом канале."
        f"{link_text}"
    )

    subscribers = await get_subscribers_for_source(session, post.source_id)
    for user in subscribers:
        alert = await create_alert(session, user.id, post.id, "reactions", reason)
        await _send_alert_to_user(bot, session, user.id, reason)


async def _send_alert_to_user(bot: Bot, session: AsyncSession, user_id: int, text: str):
    """Send alert message to all Telegram accounts linked to the user."""
    telegram_ids = await get_telegram_ids_for_user(session, user_id)
    for tg_id in telegram_ids:
        try:
            await bot.send_message(tg_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send alert to tg_id={tg_id}: {e}")
