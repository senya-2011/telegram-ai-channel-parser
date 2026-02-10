import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Post
from app.db.repositories import find_similar_posts, get_source_by_id
from app.services.embedding import cosine_similarity
from app.services.llm_client import check_similarity

logger = logging.getLogger(__name__)


async def find_confirmed_similar_posts(
    session: AsyncSession,
    post: Post,
) -> list[dict]:
    """
    Find posts that are similar to the given post.
    1. Use pgvector to find embedding-similar posts
    2. Confirm with LLM

    Returns list of {"post": Post, "source_title": str, "explanation": str}
    """
    if not post.embedding or not post.summary:
        return []

    # Step 1: Vector search via pgvector
    candidates = await find_similar_posts(
        session,
        embedding=post.embedding,
        threshold=settings.similarity_threshold,
        hours=48,
        exclude_post_id=post.id,
    )

    if not candidates:
        return []

    # Filter by cosine similarity threshold
    similar_candidates = []
    for candidate in candidates:
        if candidate.embedding:
            sim = cosine_similarity(post.embedding, candidate.embedding)
            if sim >= settings.similarity_threshold:
                similar_candidates.append((candidate, sim))

    if not similar_candidates:
        return []

    logger.info(
        f"Found {len(similar_candidates)} similar candidates for post {post.id}, "
        f"confirming with LLM..."
    )

    # Step 2: LLM confirmation
    confirmed = []
    for candidate, sim_score in similar_candidates[:5]:  # Limit LLM calls
        if not candidate.summary or not post.summary:
            continue

        result = await check_similarity(post.summary, candidate.summary)

        if result["is_similar"]:
            source = await get_source_by_id(session, candidate.source_id)
            source_title = source.title or source.identifier if source else "Неизвестный"
            confirmed.append({
                "post": candidate,
                "source_title": source_title,
                "explanation": result["explanation"],
                "similarity_score": sim_score,
            })

    return confirmed
