import logging
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

from app.config import settings

logger = logging.getLogger(__name__)


def _url_to_feed_root(url: str) -> str:
    """
    Convert an article URL to the site's feed/section root.
    https://example.com/blog/article-123 -> https://example.com/blog/
    https://habr.com/ru/articles/12345/ -> https://habr.com/ru/
    """
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # If path has article-like segments, strip them
    # Keep only first 1-2 meaningful path segments
    clean_parts = []
    for part in path_parts:
        # Stop at segments that look like article IDs or slugs
        if re.match(r'^\d{4,}$', part):  # numeric ID like 12345
            break
        if len(part) > 40:  # long slug like "my-article-about-ai-2024"
            break
        if re.match(r'^[\d]{4}-[\d]{2}', part):  # date like 2024-01
            break
        clean_parts.append(part)
        if len(clean_parts) >= 2:
            break

    clean_path = "/" + "/".join(clean_parts) + "/" if clean_parts else "/"
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


async def search_related_sources(topics: list[str], max_results: int = 8) -> list[dict]:
    """
    Search for web sources AND Telegram channels related to given topics.
    Returns list of {"title": str, "url": str, "snippet": str, "type": "web"|"telegram"}
    """
    seen = set()  # Track seen domains/channels to avoid duplicates

    # Part 1: Search Telegram channels via Telethon (direct, no API needed)
    tg_results = []
    raw_tg = await _search_telegram_channels(topics)
    for r in raw_tg:
        key = r["title"].lower()
        if key not in seen:
            seen.add(key)
            tg_results.append(r)

    # Part 2: Search web via Tavily
    web_results = []
    if settings.tavily_api_key:
        web_results = await _search_web_tavily(topics, seen)
    else:
        logger.warning("Tavily API key not set, only Telegram search available")

    # Balance: reserve half for each type, fill remaining with the other
    half = max_results // 2
    tg_limited = tg_results[:half]
    web_limited = web_results[:half]
    # Fill remaining slots
    remaining = max_results - len(tg_limited) - len(web_limited)
    if remaining > 0:
        extra_tg = tg_results[half:half + remaining]
        extra_web = web_results[half:half + remaining]
        tg_limited.extend(extra_tg[:remaining - len(extra_web)])
        web_limited.extend(extra_web[:remaining - len(extra_tg)])

    # Interleave: telegram first, then web
    results = tg_limited + web_limited
    return results[:max_results]


async def _search_telegram_channels(topics: list[str]) -> list[dict]:
    """Search for Telegram channels using Telethon's global search."""
    results = []

    try:
        from app.services.telegram_parser import get_telethon_client
        from telethon.tl.functions.contacts import SearchRequest
        from telethon.tl.types import Channel

        client = await get_telethon_client()

        # Search with multiple queries for better coverage
        queries = [
            f"AI {topics[0]}" if topics else "AI news",
            "нейросети AI новости",
            f"{topics[1]} channel" if len(topics) > 1 else "machine learning",
        ]

        seen_ids = set()
        for query in queries:
            try:
                result = await client(SearchRequest(q=query, limit=10))
                for chat in result.chats:
                    if isinstance(chat, Channel) and chat.username and chat.id not in seen_ids:
                        seen_ids.add(chat.id)
                        subs = chat.participants_count or 0
                        results.append({
                            "title": f"@{chat.username}",
                            "url": f"https://t.me/{chat.username}",
                            "snippet": f"{chat.title} — {subs:,} подписчиков",
                            "type": "telegram",
                        })
            except Exception as e:
                logger.debug(f"Telethon search failed for '{query}': {e}")

        # Sort by subscriber count (extract from snippet)
        logger.info(f"Telethon found {len(results)} channels for topics: {topics[:3]}")

    except Exception as e:
        logger.error(f"Telegram channel search error: {e}")

    return results


async def _search_web_tavily(topics: list[str], seen: set) -> list[dict]:
    """Search web sources via Tavily, extracting feed roots instead of article URLs."""
    results = []

    try:
        from tavily import AsyncTavilyClient
        client = AsyncTavilyClient(api_key=settings.tavily_api_key)

        query = f"AI news blog: {', '.join(topics[:3])}"
        response = await client.search(
            query=query,
            search_depth="basic",
            max_results=10,
            include_answer=False,
        )

        for item in response.get("results", []):
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("content", "")[:150]

            if not url or not title:
                continue

            parsed = urlparse(url)
            domain = parsed.netloc

            # Skip Telegram links (handled separately)
            if "t.me" in domain:
                continue

            # Convert article URL to feed root
            feed_url = _url_to_feed_root(url)
            feed_domain = urlparse(feed_url).netloc

            if feed_domain in seen:
                continue
            seen.add(feed_domain)

            # Use domain as title if page title is too article-specific
            site_name = feed_domain.replace("www.", "")

            results.append({
                "title": site_name,
                "url": feed_url,
                "snippet": snippet,
                "type": "web",
            })

        logger.info(f"Tavily found {len(results)} web sources for topics: {topics[:3]}")

    except Exception as e:
        logger.error(f"Tavily search error: {e}")

    return results


async def extract_topics_from_summaries(summaries: list[str]) -> list[str]:
    """Use DeepSeek to extract key topics/keywords from post summaries."""
    from app.services.llm_client import get_llm_client

    if not summaries:
        return []

    client = get_llm_client()
    combined = "\n".join(summaries[:10])

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Извлеки 3-5 ключевых тем/ключевых слов из этих новостей. "
                        "Ответь ТОЛЬКО списком через запятую, без нумерации и пояснений. "
                        "Пиши на английском для лучшего поиска."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Новости:\n{combined}",
                },
            ],
            max_tokens=100,
            temperature=0.2,
        )
        text = response.choices[0].message.content.strip()
        topics = [t.strip() for t in text.split(",") if t.strip()]
        return topics[:5]
    except Exception as e:
        logger.error(f"Topic extraction error: {e}")
        return ["AI news", "artificial intelligence", "machine learning"]
