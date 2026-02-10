import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import get_posts_for_digest, get_source_by_id, get_user_sources
from app.services.llm_client import generate_digest_text

logger = logging.getLogger(__name__)


async def generate_digest_for_user(session: AsyncSession, user_id: int) -> Optional[str]:
    """
    Generate a daily digest for a specific user.
    Collects top posts from the user's subscribed sources,
    then uses LLM to create a formatted digest.
    """
    # Get user's source IDs
    sources = await get_user_sources(session, user_id)
    if not sources:
        return None

    source_ids = [s.id for s in sources]

    # Get top posts from the last 24 hours
    posts = await get_posts_for_digest(session, source_ids, hours=24, limit=20)

    if not posts:
        return None

    # Prepare summaries for LLM
    summaries = []
    for post in posts:
        source = await get_source_by_id(session, post.source_id)
        source_title = source.title or source.identifier if source else "Неизвестный"

        summaries.append({
            "source": source_title,
            "summary": post.summary or post.content[:300],
            "reactions": post.reactions_count,
        })

    # Generate digest via LLM
    digest_text = await generate_digest_text(summaries)

    if not digest_text:
        # Fallback: simple list
        digest_text = "📰 **Дайджест за сегодня:**\n\n"
        for i, s in enumerate(summaries[:10], 1):
            digest_text += f"{i}. **[{s['source']}]** (👍 {s['reactions']})\n{s['summary']}\n\n"

    return digest_text
