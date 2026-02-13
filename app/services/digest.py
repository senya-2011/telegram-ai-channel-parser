import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import get_clusters_by_ids, get_posts_for_digest, get_source_by_id, get_user_sources
from app.services.llm_client import analyze_business_impact, generate_digest_text

logger = logging.getLogger(__name__)


def _escape_md_url(url: str) -> str:
    """Escape parentheses in URLs so Markdown links don't break."""
    return url.replace("(", "%28").replace(")", "%29")


def _clean_url(url: str) -> str:
    """Remove UTM parameters and other tracking garbage from URLs."""
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        # Remove common tracking params
        clean_params = {k: v for k, v in params.items() if not k.startswith(("utm_", "ref", "source"))}
        clean_query = urlencode(clean_params, doseq=True)
        return urlunparse(parsed._replace(query=clean_query))
    except Exception:
        return url


def _get_post_link(source, post) -> str:
    """Generate a link to the original post/article."""
    if source and source.type == "telegram":
        channel = source.identifier.lstrip("@")
        return f"https://t.me/{channel}/{post.external_id}"
    elif post.external_id and post.external_id.startswith("http"):
        return _escape_md_url(_clean_url(post.external_id))
    return ""


def _short_headline(text: str, limit: int = 72) -> str:
    raw = (text or "").strip().replace("\n", " ")
    if not raw:
        return "Подробнее"
    sentence = raw.split(".")[0].strip()
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(" ", 1)[0].strip() + "..."
    return sentence or "Подробнее"


def _priority_rank(priority: str) -> int:
    mapping = {"low": 1, "medium": 2, "high": 3}
    return mapping.get((priority or "low").lower(), 1)


def _trim_text(text: str, limit: int = 120) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    short = value[:limit].rsplit(" ", 1)[0].strip()
    return (short or value[:limit]).rstrip(".,;:") + "..."


def _inject_curated_links_inline(digest_text: str, items: list[dict]) -> str:
    """
    Add "Подробнее" line under each bullet in curated sections:
    - "Главное"
    - "Также интересно"
    Mapping is positional: bullets in these sections are mapped to items with links.
    """
    if not digest_text or not items:
        return digest_text

    lines = digest_text.split("\n")
    out: list[str] = []
    in_curated = False
    item_idx = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🔥 <b>Главное:</b>"):
            in_curated = True
            out.append(line)
            continue
        if stripped.startswith("📌 <b>Также интересно:"):
            in_curated = True
            out.append(line)
            continue
        if stripped.startswith("🧷 <b>Новости по источникам:</b>"):
            in_curated = False
            out.append(line)
            continue

        out.append(line)

        if in_curated and stripped.startswith("- "):
            while item_idx < len(items) and not items[item_idx].get("link"):
                item_idx += 1
            if item_idx < len(items):
                link = items[item_idx]["link"]
                title = _short_headline(items[item_idx].get("summary", ""), limit=56)
                out.append(f'🔗 <a href="{link}">Подробнее: {title}</a>')
                item_idx += 1

    return "\n".join(out)


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

    # Keep one representative post per cluster to avoid duplicate copy-pastes in digest.
    unique_posts = []
    seen_keys = set()
    for post in posts:
        dedup_key = f"cluster:{post.cluster_id}" if post.cluster_id else f"post:{post.id}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        unique_posts.append(post)

    cluster_ids = [p.cluster_id for p in unique_posts if p.cluster_id]
    clusters_map = await get_clusters_by_ids(session, cluster_ids)

    def _post_sort_key(post):
        cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
        kind_weight = {"product": 4, "trend": 3, "research": 2, "misc": 1}.get(
            (cluster.news_kind if cluster else "misc"),
            1,
        )
        priority_weight = _priority_rank(cluster.priority if cluster else "low")
        product_score = float(cluster.product_score) if cluster else 0.0
        mentions = int(cluster.mention_count) if cluster else 1
        return (kind_weight, priority_weight, product_score, mentions, post.reactions_count)

    unique_posts.sort(key=_post_sort_key, reverse=True)

    target_items = max(6, settings.digest_target_items)
    product_target = max(1, int(target_items * settings.digest_product_share))
    non_product_cap = max(1, settings.digest_max_non_product)

    product_posts = []
    trend_posts = []
    research_posts = []
    misc_posts = []
    for post in unique_posts:
        cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
        kind = cluster.news_kind if cluster else "misc"
        if kind == "product":
            product_posts.append(post)
        elif kind == "trend":
            trend_posts.append(post)
        elif kind == "research":
            research_posts.append(post)
        else:
            misc_posts.append(post)

    selected_posts = product_posts[:product_target]
    non_product = trend_posts + research_posts + misc_posts
    selected_posts.extend(non_product[:non_product_cap])
    if len(selected_posts) < target_items:
        already = {p.id for p in selected_posts}
        for post in unique_posts:
            if post.id in already:
                continue
            selected_posts.append(post)
            if len(selected_posts) >= target_items:
                break

    # Prepare summaries for LLM — fast local keyword filter (LLM check already done at processing time)
    _AI_KW = {
        "ai", "artificial intelligence", "ml", "machine learning", "deep learning",
        "neural", "llm", "gpt", "chatgpt", "openai", "deepseek", "gemini", "claude",
        "transformer", "diffusion", "нейросет", "нейронн", "искусственн",
        "машинн обучен", "ии ", "language model", "nlp", "rag", "embedding",
        "copilot", "midjourney", "hugging face", "модел", "автоматизац",
    }

    def _is_ai(text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in _AI_KW)

    summaries = []
    for post in selected_posts:
        post_text = post.summary or post.content[:300]

        # Fast keyword filter — skip obvious non-AI posts
        if not _is_ai(post_text):
            logger.debug(f"Digest: skipping post {post.id} — not AI-relevant (keyword filter)")
            continue

        source = await get_source_by_id(session, post.source_id)
        source_title = source.title or source.identifier if source else "Неизвестный"
        link = _get_post_link(source, post)
        cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
        mentions = cluster.mention_count if cluster else 1
        tags_text = " ".join(tag for tag in (cluster.tags or "").split(",") if tag) if cluster else "#AIТехнологии"

        summaries.append({
            "source": source_title,
            "summary": post_text,
            "reactions": post.reactions_count,
            "link": link,
            "mentions": mentions,
            "tags": tags_text,
        })

    # Generate digest via LLM
    digest_text = await generate_digest_text(summaries)

    if digest_text:
        digest_text = _inject_curated_links_inline(digest_text, summaries)
        business_block = await _build_digest_business_impact_block(summaries)

        # Deterministic per-news section with inline tags (LLM output may reorder/omit markers).
        per_news_section = "\n\n🧷 <b>Новости по источникам:</b>\n"
        for i, s in enumerate(summaries[:6], 1):
            mentions_text = f" | 📈 {s['mentions']} источн." if s.get("mentions", 1) >= 2 else ""
            short_summary = _trim_text(s["summary"] or "", 120)
            link_text = f'\n🔗 <a href="{s["link"]}">Оригинал</a>' if s.get("link") else ""
            per_news_section += (
                f'{i}. <b>{s["source"]}</b>\n'
                f'🏷 {s.get("tags") or "#AIТехнологии"}{mentions_text}\n'
                f'{short_summary}{link_text}\n\n'
            )
        digest_text += business_block + per_news_section
    else:
        # Fallback: simple list with links, HTML format
        digest_text = "📰 <b>Дайджест за сегодня:</b>\n\n"
        for i, s in enumerate(summaries[:10], 1):
            link_text = f'\n🔗 <a href="{s["link"]}">Оригинал</a>' if s["link"] else ""
            mentions_text = f", {s['mentions']} источн." if s.get("mentions", 1) >= 2 else ""
            digest_text += f'{i}. <b>{s["source"]}</b> (👍 {s["reactions"]}{mentions_text})\n{s["summary"]}{link_text}\n\n'

    return digest_text


async def _build_digest_business_impact_block(summaries: list[dict]) -> str:
    if not settings.tavily_api_key or not summaries:
        return ""

    candidates = sorted(
        summaries,
        key=lambda s: (s.get("mentions", 1), s.get("reactions", 0)),
        reverse=True,
    )[:2]

    if not candidates:
        return ""

    try:
        from tavily import AsyncTavilyClient
    except Exception:
        return ""

    client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    lines = ["\n\n🏢 <b>Влияние на бизнес (прецеденты):</b>"]

    for i, item in enumerate(candidates, 1):
        summary = item.get("summary", "")
        try:
            response = await client.search(
                query=f"{summary[:180]} business impact case",
                search_depth="basic",
                max_results=min(4, settings.business_impact_max_sources),
                include_answer=False,
            )
            contexts = []
            for res in response.get("results", [])[: min(4, settings.business_impact_max_sources)]:
                contexts.append({
                    "title": res.get("title", "")[:110],
                    "snippet": res.get("content", "")[:220],
                    "url": res.get("url", ""),
                })
            if not contexts:
                continue
            analysis = await analyze_business_impact(summary, contexts)
            positives = analysis.get("positive_precedents", [])[:1]
            negatives = analysis.get("negative_precedents", [])[:1]
            score = float(analysis.get("impact_score", 0.0))

            lines.append(f"{i}. <b>{item.get('source', 'Источник')}</b> — score {score:.2f}")
            if positives:
                lines.append(f"✅ {_trim_text(positives[0], 130)}")
            if negatives:
                lines.append(f"⚠️ {_trim_text(negatives[0], 130)}")
            ref_url = contexts[0].get("url")
            if ref_url:
                lines.append(f'🔗 <a href="{ref_url}">Прецедент</a>')
            lines.append("")
        except Exception as e:
            logger.debug(f"Digest business impact failed for item {i}: {e}")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
