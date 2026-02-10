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
from app.services.llm_client import check_ai_relevance, summarize_post
from app.services.similarity import find_confirmed_similar_posts

logger = logging.getLogger(__name__)

# Keywords to filter Tavily results — only AI-related content passes
_AI_KEYWORDS = {
    "ai", "artificial intelligence", "ml", "machine learning", "deep learning",
    "neural network", "neural net", "llm", "gpt", "chatgpt", "openai", "deepseek",
    "gemini", "claude", "transformer", "diffusion", "генеративн", "нейросет",
    "нейронн", "искусственн интеллект", "машинн обучен", "ии ", "ии-",
    "language model", "computer vision", "nlp", "rag", "fine-tun", "embedding",
    "автоматизац", "робот", "copilot", "midjourney", "stable diffusion", "hugging face",
}


def _is_ai_related(text: str) -> bool:
    """Check if text contains AI-related keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _AI_KEYWORDS)


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
    1. Analyze all posts (summary, embedding, relevance)
    2. Group similar posts into clusters — one alert per cluster
    3. Check for reaction anomalies
    """
    unprocessed = await get_unprocessed_posts(session, limit=30)

    if not unprocessed:
        logger.debug("No unprocessed posts found")
        return

    logger.info(f"Processing {len(unprocessed)} new posts...")

    # Phase 1: Analyze all posts (summary, embedding, AI relevance)
    relevant_posts: list[Post] = []
    for post in unprocessed:
        try:
            is_relevant = await _analyze_post(session, post)
            if is_relevant:
                relevant_posts.append(post)
        except Exception as e:
            logger.error(f"Error analyzing post {post.id}: {e}")

    if not relevant_posts:
        return

    # Phase 2: Group similar posts into clusters, send ONE alert per cluster
    alerted_post_ids: set[int] = set()  # track which posts already got a similarity alert

    for post in relevant_posts:
        if post.id in alerted_post_ids:
            continue  # already included in a cluster alert

        if post.embedding is None:
            continue

        try:
            similar_posts = await find_confirmed_similar_posts(session, post)
            if not similar_posts:
                continue

            # Collect all posts from this batch that are also similar to this one
            # This merges e.g. post A, B, C about the same news into one alert
            cluster_similar = list(similar_posts)
            cluster_post_ids = {post.id} | {sp["post"].id for sp in similar_posts}

            # Check if any other unprocessed post in this batch is also similar
            for other_post in relevant_posts:
                if other_post.id in cluster_post_ids:
                    continue
                if other_post.embedding is None or not other_post.summary:
                    continue
                # Check if this other post is similar to any post in the cluster
                from app.services.embedding import cosine_similarity
                embedding_list = list(post.embedding) if not isinstance(post.embedding, list) else post.embedding
                other_emb = list(other_post.embedding) if not isinstance(other_post.embedding, list) else other_post.embedding
                sim = cosine_similarity(embedding_list, other_emb)
                if sim >= settings.similarity_threshold:
                    source = await get_source_by_id(session, other_post.source_id)
                    source_title = source.title or source.identifier if source else "Неизвестный"
                    cluster_similar.append({
                        "post": other_post,
                        "source_title": source_title,
                        "explanation": "",
                        "similarity_score": sim,
                    })
                    cluster_post_ids.add(other_post.id)

            # Mark all posts in this cluster as alerted
            alerted_post_ids.update(cluster_post_ids)

            # Send ONE consolidated alert for the whole cluster
            await _send_similarity_alert(session, bot, post, cluster_similar)

        except Exception as e:
            logger.error(f"Error checking similarity for post {post.id}: {e}")

    # Phase 3: Check reaction anomalies (independent of similarity)
    for post in relevant_posts:
        try:
            if post.reactions_ratio and post.reactions_ratio >= settings.reactions_multiplier:
                await _send_reactions_alert(session, bot, post, post.reactions_ratio)
        except Exception as e:
            logger.error(f"Error sending reaction alert for post {post.id}: {e}")


async def _analyze_post(session: AsyncSession, post: Post) -> bool:
    """
    Analyze a single post: summary, AI relevance, embedding, reactions.
    Returns True if post is AI-relevant and should be checked for alerts.
    """
    # Step 1: Generate summary
    summary = await summarize_post(post.content)
    if not summary:
        summary = post.content[:300] + "..." if len(post.content) > 300 else post.content

    # Step 2: Check AI relevance — filter out ads, promos, off-topic
    is_relevant = await check_ai_relevance(summary)

    # Step 3: Generate embedding
    text_for_embedding = summary or post.content
    embedding = generate_embedding(text_for_embedding)

    # Step 4: Check reaction anomaly ratio
    reactions_ratio = None
    if post.reactions_count > 0:
        avg_reactions = await get_avg_reactions_for_source(session, post.source_id, days=7)
        if avg_reactions > 0:
            reactions_ratio = post.reactions_count / avg_reactions

    # Save analysis results (always, even if filtered)
    await update_post_analysis(
        session,
        post_id=post.id,
        summary=summary,
        embedding=embedding,
        reactions_ratio=reactions_ratio,
    )

    # Update post object in memory
    post.summary = summary
    post.embedding = embedding
    post.reactions_ratio = reactions_ratio

    if not is_relevant:
        logger.info(f"Post {post.id} filtered out — not AI-relevant (ad/promo/off-topic)")
        return False

    return True


async def _enrich_alert_with_search(summary: str) -> str:
    """Search the web for additional context about the alert topic."""
    try:
        if not settings.tavily_api_key:
            return ""
        from tavily import AsyncTavilyClient
        client = AsyncTavilyClient(api_key=settings.tavily_api_key)
        response = await client.search(
            query=summary[:200],
            search_depth="basic",
            max_results=3,
            include_answer=True,
        )
        extra = ""
        answer = response.get("answer")
        if answer:
            extra += f"\n\n🌐 <b>Что пишут другие источники:</b>\n{answer[:400]}\n"
        results = response.get("results", [])[:6]  # fetch more, then filter
        if results:
            # Filter: only AI-related results
            ai_results = [
                r for r in results
                if _is_ai_related(r.get("title", "") + " " + r.get("content", ""))
            ][:3]
            if ai_results:
                extra += "\n📎 <b>Из других источников:</b>\n"
                for r in ai_results:
                    title = r.get("title", "")[:60]
                    url = _escape_md_url(r.get("url", ""))
                    snippet = r.get("content", "")[:120]
                    if snippet:
                        extra += f'• <a href="{url}">{title}</a>\n  <i>{snippet}</i>\n'
                    else:
                        extra += f'• <a href="{url}">{title}</a>\n'
        return extra
    except Exception as e:
        logger.debug(f"Alert enrichment failed: {e}")
        return ""


async def _send_similarity_alert(
    session: AsyncSession, bot: Bot, post: Post, similar_posts: list[dict]
):
    """Create and send alerts for similar posts across channels."""
    source = await get_source_by_id(session, post.source_id)
    source_title = source.title or source.identifier if source else "Неизвестный"
    post_link = _get_post_link(source, post)

    similar_sources = [s["source_title"] for s in similar_posts]
    explanation = similar_posts[0]["explanation"] if similar_posts else ""

    # Build links for all similar posts (HTML format)
    links_text = ""
    if post_link:
        links_text += f'🔗 <a href="{post_link}">{source_title}</a>\n'
    for sp in similar_posts:
        sp_source = await get_source_by_id(session, sp["post"].source_id)
        sp_link = _get_post_link(sp_source, sp["post"])
        if sp_link:
            links_text += f'🔗 <a href="{sp_link}">{sp["source_title"]}</a>\n'

    # Enrich with web search
    extra_context = await _enrich_alert_with_search(post.summary or post.content[:200])

    reason = (
        f"🔔 Похожая новость найдена в нескольких каналах!\n\n"
        f"📰 <b>Новость:</b> {post.summary[:200]}\n\n"
        f"📡 <b>Источники:</b> {source_title}, {', '.join(similar_sources)}\n\n"
        f"{links_text}\n"
        f"💡 <b>Анализ:</b> {explanation}"
        f"{extra_context}"
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

    alert_topic = post.summary[:100] if post.summary else post.content[:100]
    for user in subscribers:
        alert = await create_alert(session, user.id, post.id, "similar", reason)
        await _send_alert_to_user(bot, session, user.id, reason, topic=alert_topic)


async def _send_reactions_alert(
    session: AsyncSession, bot: Bot, post: Post, reactions_ratio: float
):
    """Create and send alerts for posts with abnormally high reactions."""
    source = await get_source_by_id(session, post.source_id)
    source_title = source.title or source.identifier if source else "Неизвестный"
    post_link = _get_post_link(source, post)
    link_text = f'\n🔗 <a href="{post_link}">Открыть оригинал</a>' if post_link else ""

    # Enrich with web search
    extra_context = await _enrich_alert_with_search(post.summary or post.content[:200])

    reason = (
        f"🔥 Пост с аномально высокой активностью!\n\n"
        f"📰 <b>Новость:</b> {post.summary[:200]}\n\n"
        f"📡 <b>Канал:</b> {source_title}\n"
        f"👍 <b>Реакций:</b> {post.reactions_count} "
        f"(в {reactions_ratio:.1f}x раз больше среднего)\n\n"
        f"💡 Этот пост вызвал значительно больше реакций, чем обычные посты в этом канале."
        f"{link_text}"
        f"{extra_context}"
    )

    alert_topic = post.summary[:100] if post.summary else post.content[:100]
    subscribers = await get_subscribers_for_source(session, post.source_id)
    for user in subscribers:
        alert = await create_alert(session, user.id, post.id, "reactions", reason)
        await _send_alert_to_user(bot, session, user.id, reason, topic=alert_topic)


async def _send_alert_to_user(bot: Bot, session: AsyncSession, user_id: int, text: str, topic: str = ""):
    """Send alert message to all Telegram accounts linked to the user."""
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
