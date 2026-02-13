import logging
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BLOCKED_SOURCE_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "tiktok.com",
    "www.tiktok.com",
    "instagram.com",
    "www.instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
}


def _is_parseable_source_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    return host not in _BLOCKED_SOURCE_DOMAINS


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

    # Part 3: API-friendly platforms (Reddit/GitHub/Product Hunt)
    api_results = await _search_api_sources(topics, seen)

    # Balanced allocation so API sources are visible too.
    # For max_results=8 => tg/web/api caps: 3/3/2
    tg_cap = max(1, (max_results + 2) // 3)
    web_cap = max(1, (max_results + 1) // 3)
    api_cap = max_results - tg_cap - web_cap
    api_cap = max(api_cap, 1)

    tg_limited = tg_results[:tg_cap]
    web_limited = web_results[:web_cap]
    api_limited = api_results[:api_cap]

    # Fill remaining slots from leftovers by availability
    results = tg_limited + web_limited + api_limited
    if len(results) < max_results:
        leftovers = tg_results[tg_cap:] + web_results[web_cap:] + api_results[api_cap:]
        results.extend(leftovers[: max_results - len(results)])

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

            # Skip Telegram links (handled separately) and blocked domains
            if "t.me" in domain or not _is_parseable_source_url(url):
                continue

            # Convert article URL to feed root
            feed_url = _url_to_feed_root(url)
            feed_domain = urlparse(feed_url).netloc

            if feed_domain in seen:
                continue
            if not _is_parseable_source_url(feed_url):
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


async def _search_api_sources(topics: list[str], seen: set) -> list[dict]:
    results: list[dict] = []
    results.extend(await _search_reddit_sources(topics, seen))
    results.extend(await _search_github_sources(topics, seen))
    results.extend(await _search_product_hunt_sources(seen))
    return results


async def _search_reddit_sources(topics: list[str], seen: set) -> list[dict]:
    query = " ".join(topics[:2]) or "artificial intelligence"
    found: list[dict] = []
    headers = {"User-Agent": settings.reddit_user_agent or "telegram-ai-parser/1.0"}
    token = await _get_reddit_access_token()
    if token:
        url = "https://oauth.reddit.com/subreddits/search"
        headers["Authorization"] = f"Bearer {token}"
        params = {"q": query, "limit": 6, "include_over_18": "false"}
    else:
        # Fallback for cases when Reddit OAuth credentials are not configured yet.
        url = "https://www.reddit.com/subreddits/search.json"
        params = {"q": query, "limit": 6}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.get(
                url,
                params=params,
                headers=headers,
            )
            if resp.status_code != 200:
                return found
            payload = resp.json()
            for item in payload.get("data", {}).get("children", []):
                data = item.get("data", {})
                sub = data.get("display_name")
                title = data.get("title", "")
                if not sub:
                    continue
                source_url = f"https://www.reddit.com/r/{sub}/.rss"
                key = f"reddit:{sub.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "title": f"r/{sub}",
                    "url": source_url,
                    "identifier": sub,
                    "snippet": title[:140],
                    "type": "reddit",
                })
        except Exception as e:
            logger.debug(f"Reddit source discovery failed: {e}")
    return found


async def _search_github_sources(topics: list[str], seen: set) -> list[dict]:
    query = " ".join(topics[:2]) or "llm ai"
    url = "https://api.github.com/search/repositories"
    found: list[dict] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            headers = {"Accept": "application/vnd.github+json"}
            if settings.github_api_key:
                headers["Authorization"] = f"Bearer {settings.github_api_key}"
            resp = await client.get(
                url,
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 5},
                headers=headers,
            )
            if resp.status_code != 200:
                return found
            payload = resp.json()
            for repo in payload.get("items", []):
                full_name = repo.get("full_name")
                html_url = repo.get("html_url")
                if not full_name or not html_url:
                    continue
                source_url = f"{html_url}/releases.atom"
                key = f"github:{full_name.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "title": full_name,
                    "url": source_url,
                    "identifier": full_name,
                    "snippet": (repo.get("description") or "")[:140],
                    "type": "github",
                })
        except Exception as e:
            logger.debug(f"GitHub source discovery failed: {e}")
    return found


async def _search_product_hunt_sources(seen: set) -> list[dict]:
    if not settings.producthunt_api_key:
        key = "producthunt:feed"
        if key in seen:
            return []
        seen.add(key)
        return [
            {
                "title": "Product Hunt",
                "url": "https://www.producthunt.com/feed",
                "snippet": "Новые продукты и запуски",
                "type": "web",
            }
        ]

    query = """
    {
      posts(first: 5) {
        nodes {
          name
          tagline
          website
        }
      }
    }
    """
    found: list[dict] = []
    headers = {
        "Authorization": f"Bearer {settings.producthunt_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.post(
                "https://api.producthunt.com/v2/api/graphql",
                headers=headers,
                json={"query": query},
            )
            if resp.status_code != 200:
                return found
            payload = resp.json()
            posts = payload.get("data", {}).get("posts", {}).get("nodes", [])
            for post in posts:
                site = post.get("website")
                name = post.get("name")
                tagline = post.get("tagline", "")
                if not site or not name or not _is_parseable_source_url(site):
                    continue
                key = f"ph:{urlparse(site).netloc.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "title": name,
                    "url": _url_to_feed_root(site),
                    "identifier": "ai",
                    "snippet": tagline[:140],
                    "type": "producthunt",
                })
        except Exception as e:
            logger.debug(f"Product Hunt source discovery failed: {e}")
    return found


async def _get_reddit_access_token() -> str:
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return ""
    token_url = "https://www.reddit.com/api/v1/access_token"
    auth = (settings.reddit_client_id, settings.reddit_client_secret)
    headers = {"User-Agent": settings.reddit_user_agent or "telegram-ai-parser/1.0"}
    data = {"grant_type": "client_credentials"}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            resp = await client.post(token_url, headers=headers, data=data, auth=auth)
            if resp.status_code != 200:
                return ""
            payload = resp.json()
            return payload.get("access_token", "")
        except Exception as e:
            logger.debug(f"Reddit token request failed: {e}")
            return ""


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
