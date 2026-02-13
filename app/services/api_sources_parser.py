import datetime
import logging
import asyncio

import httpx

from app.config import settings
from app.db.database import async_session
from app.db.repositories import create_post, get_all_sources, get_existing_external_ids

logger = logging.getLogger(__name__)

_AI_KW = {
    "ai", "artificial intelligence", "machine learning", "ml", "llm", "gpt", "openai",
    "deepseek", "anthropic", "gemini", "claude", "neural", "rag", "agent",
    "нейросет", "искусствен", "машинн обучен", "ии ", "модель",
}


def _is_ai_candidate(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in _AI_KW)


def _to_naive_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo:
        return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def _parse_iso_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _to_naive_utc(dt)
    except Exception:
        return None


def _lookback_cutoff() -> datetime.datetime:
    return datetime.datetime.utcnow() - datetime.timedelta(hours=settings.api_source_lookback_hours)


async def parse_api_sources() -> None:
    logger.info("Starting API sources parsing...")
    async with async_session() as session:
        reddit_sources = list(await get_all_sources(session, source_type="reddit"))
        github_sources = list(await get_all_sources(session, source_type="github"))
        hunt_sources = list(await get_all_sources(session, source_type="producthunt"))

    sources_plan = (
        [("reddit", s.id, s.identifier) for s in reddit_sources]
        + [("github", s.id, s.identifier) for s in github_sources]
        + [("producthunt", s.id, s.identifier) for s in hunt_sources]
    )

    if not sources_plan:
        logger.info("No API sources configured")
        return

    logger.info(
        "API sources queued: reddit=%s github=%s producthunt=%s total=%s",
        len(reddit_sources),
        len(github_sources),
        len(hunt_sources),
        len(sources_plan),
    )

    semaphore = asyncio.Semaphore(4)

    async def _worker(source_type: str, source_id: int, identifier: str) -> int:
        async with semaphore:
            return await _parse_single_source_with_timeout(source_type, source_id, identifier)

    results = await asyncio.gather(
        *[_worker(st, sid, ident) for st, sid, ident in sources_plan],
        return_exceptions=True,
    )

    total_new = 0
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            logger.error(f"API source parse failed: {result}")
        else:
            total_new += int(result or 0)
    logger.info("API sources parsing done: new_posts=%s failed=%s", total_new, failed)


async def _parse_single_source_with_timeout(source_type: str, source_id: int, identifier: str) -> int:
    try:
        logger.info("API parse started: [%s] %s", source_type, identifier)
        count = await asyncio.wait_for(
            _parse_single_source(source_type, source_id, identifier),
            timeout=35.0,
        )
        logger.info("API parse finished: [%s] %s -> new=%s", source_type, identifier, count)
        return count
    except asyncio.TimeoutError:
        logger.warning("API parse timeout: [%s] %s", source_type, identifier)
        return 0
    except Exception as e:
        logger.error("API parse error: [%s] %s -> %s", source_type, identifier, e)
        return 0


async def _parse_single_source(source_type: str, source_id: int, identifier: str) -> int:
    async with async_session() as session:
        if source_type == "reddit":
            return await _parse_reddit_source(session, source_id, identifier)
        if source_type == "github":
            return await _parse_github_source(session, source_id, identifier)
        if source_type == "producthunt":
            return await _parse_producthunt_source(session, source_id, identifier)
    return 0


async def _parse_reddit_source(session, source_id: int, identifier: str) -> int:
    subreddit = identifier.strip().replace("r/", "").replace("/r/", "").replace("/", "")
    if not subreddit:
        return 0

    token = await _get_reddit_access_token()
    headers = {"User-Agent": settings.reddit_user_agent or "telegram-ai-parser/1.0"}
    if token:
        url = f"https://oauth.reddit.com/r/{subreddit}/new"
        headers["Authorization"] = f"Bearer {token}"
    else:
        # Public fallback keeps ingestion alive without OAuth keys.
        url = f"https://www.reddit.com/r/{subreddit}/new.json"

    params = {"limit": min(settings.api_source_max_items, 50)}
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(url, params=params, headers=headers)
        if response.status_code != 200:
            return 0
        payload = response.json()

    children = payload.get("data", {}).get("children", [])
    cutoff = _lookback_cutoff()
    raw_items = []
    for child in children:
        data = child.get("data", {})
        post_id = data.get("id")
        title = data.get("title", "")
        selftext = data.get("selftext", "")
        permalink = data.get("permalink", "")
        created = data.get("created_utc")
        if not post_id or not title:
            continue
        published_at = datetime.datetime.utcfromtimestamp(created) if created else None
        if published_at and published_at < cutoff:
            continue
        text = f"{title}\n\n{selftext}".strip()
        if not _is_ai_candidate(text):
            continue
        post_url = f"https://reddit.com{permalink}" if permalink else f"https://reddit.com/r/{subreddit}"
        raw_items.append({
            "external_id": f"reddit:{subreddit}:{post_id}",
            "content": f"{title}\n\n{selftext}\n\nИсточник: {post_url}",
            "published_at": published_at,
        })

    if not raw_items:
        return 0

    existing = await get_existing_external_ids(
        session, source_id=source_id, external_ids=[item["external_id"] for item in raw_items]
    )
    new_count = 0
    for item in raw_items:
        if item["external_id"] in existing:
            continue
        post = await create_post(
            session=session,
            source_id=source_id,
            external_id=item["external_id"],
            content=item["content"][:5000],
            reactions_count=0,
            published_at=item["published_at"],
            commit=False,
        )
        if post:
            new_count += 1
    if new_count:
        await session.commit()
    return new_count


async def _parse_github_source(session, source_id: int, identifier: str) -> int:
    identifier = identifier.strip()
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_api_key:
        headers["Authorization"] = f"Bearer {settings.github_api_key}"

    items: list[dict] = []
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        if "/" in identifier:
            # Repo mode: owner/repo -> releases
            url = f"https://api.github.com/repos/{identifier}/releases"
            resp = await client.get(
                url,
                params={"per_page": min(settings.api_source_max_items, 30)},
                headers=headers,
            )
            if resp.status_code != 200:
                return 0
            releases = resp.json()
            for rel in releases:
                rel_id = rel.get("id")
                title = rel.get("name") or rel.get("tag_name") or "Release"
                body = rel.get("body") or ""
                html_url = rel.get("html_url") or f"https://github.com/{identifier}/releases"
                published_at = _parse_iso_datetime(rel.get("published_at") or rel.get("created_at"))
                text = f"{identifier} {title}\n\n{body}"
                if not rel_id or not _is_ai_candidate(text):
                    continue
                items.append({
                    "external_id": f"github:{identifier}:release:{rel_id}",
                    "content": f"{title}\n\n{body}\n\nИсточник: {html_url}",
                    "published_at": published_at,
                })
        else:
            # Query mode
            since = (datetime.datetime.utcnow() - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
            url = "https://api.github.com/search/repositories"
            resp = await client.get(
                url,
                params={
                    "q": f"{identifier} pushed:>={since}",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": min(settings.api_source_max_items, 30),
                },
                headers=headers,
            )
            if resp.status_code != 200:
                return 0
            payload = resp.json()
            for repo in payload.get("items", []):
                full_name = repo.get("full_name")
                pushed_at = repo.get("pushed_at")
                text = f"{full_name}\n{repo.get('description') or ''}\n{','.join(repo.get('topics') or [])}"
                if not full_name or not _is_ai_candidate(text):
                    continue
                items.append({
                    "external_id": f"github:{full_name}:{pushed_at}",
                    "content": (
                        f"{full_name}\n\n{repo.get('description') or 'No description'}\n"
                        f"⭐ {repo.get('stargazers_count', 0)}\n"
                        f"Источник: {repo.get('html_url')}"
                    ),
                    "published_at": _parse_iso_datetime(pushed_at),
                })

    cutoff = _lookback_cutoff()
    items = [item for item in items if (item["published_at"] is None or item["published_at"] >= cutoff)]
    if not items:
        return 0

    existing = await get_existing_external_ids(
        session, source_id=source_id, external_ids=[item["external_id"] for item in items]
    )
    new_count = 0
    for item in items:
        if item["external_id"] in existing:
            continue
        post = await create_post(
            session=session,
            source_id=source_id,
            external_id=item["external_id"],
            content=item["content"][:5000],
            reactions_count=0,
            published_at=item["published_at"],
            commit=False,
        )
        if post:
            new_count += 1
    if new_count:
        await session.commit()
    return new_count


async def _parse_producthunt_source(session, source_id: int, identifier: str) -> int:
    if not settings.producthunt_api_key:
        return 0

    keyword = identifier.strip().lower()
    query = """
    {
      posts(first: 20) {
        nodes {
          id
          name
          tagline
          website
          createdAt
        }
      }
    }
    """
    headers = {
        "Authorization": f"Bearer {settings.producthunt_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        resp = await client.post(
            "https://api.producthunt.com/v2/api/graphql",
            headers=headers,
            json={"query": query},
        )
        if resp.status_code != 200:
            return 0
        payload = resp.json()

    posts = payload.get("data", {}).get("posts", {}).get("nodes", [])
    cutoff = _lookback_cutoff()
    items = []
    for post in posts:
        post_id = post.get("id")
        name = post.get("name") or ""
        tagline = post.get("tagline") or ""
        created_at = _parse_iso_datetime(post.get("createdAt"))
        if created_at and created_at < cutoff:
            continue
        text = f"{name}\n{tagline}"
        keyword_match = bool(keyword and keyword in text.lower())
        if not keyword_match and not _is_ai_candidate(text):
            continue
        items.append({
            "external_id": f"producthunt:{post_id}",
            "content": f"{name}\n\n{tagline}\n\nИсточник: {post.get('website') or 'https://www.producthunt.com'}",
            "published_at": created_at,
        })

    if not items:
        return 0

    existing = await get_existing_external_ids(
        session, source_id=source_id, external_ids=[item["external_id"] for item in items]
    )
    new_count = 0
    for item in items:
        if item["external_id"] in existing:
            continue
        post = await create_post(
            session=session,
            source_id=source_id,
            external_id=item["external_id"],
            content=item["content"][:5000],
            reactions_count=0,
            published_at=item["published_at"],
            commit=False,
        )
        if post:
            new_count += 1
    if new_count:
        await session.commit()
    return new_count


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
        except Exception:
            return ""
