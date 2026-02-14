import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import (
    get_clusters_by_ids,
    get_posts_for_digest,
    get_source_by_id,
    get_user_disliked_clusters,
    get_user_settings,
    get_user_sources,
)
from app.services.embedding import cosine_similarity, generate_embedding
from app.services.llm_client import analyze_business_impact, generate_digest_text, score_user_prompt_relevance

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


def _is_digest_candidate(cluster) -> bool:
    if not cluster or not cluster.is_ai_relevant:
        return False

    kind = (cluster.news_kind or "misc").lower()
    priority = (cluster.priority or "low").lower()
    product_score = float(cluster.product_score or 0.0)
    core_score = float(cluster.coreai_score or 0.0)
    alert_worthy = bool(cluster.is_alert_worthy)
    implementable = bool(cluster.implementable_by_small_team)
    barrier = (cluster.infra_barrier or "high").lower()

    # В дайджест и алерты только то, что можно реализовать: реализуемо малой командой или низкий/средний инфра-барьер
    if not implementable and barrier not in {"low", "medium"}:
        return False

    if kind == "product":
        min_product = max(0.45, settings.min_product_score_for_alert - 0.05)
        return (
            alert_worthy
            or (implementable and barrier in {"low", "medium"})
            or (priority in {"high", "medium"} and product_score >= min_product)
        )
    if kind == "tech_update":
        return alert_worthy and (product_score >= 0.45 or priority in {"high", "medium"})
    if kind == "industry_report":
        return alert_worthy and core_score >= max(0.7, settings.min_non_product_core_score_for_alert)
    if kind in {"trend", "research"}:
        return alert_worthy and core_score >= settings.min_non_product_core_score_for_alert
    return False


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


async def generate_digest_for_user(
    session: AsyncSession,
    user_id: int,
    mode: str = "main",
) -> Optional[str]:
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
    user_settings = await get_user_settings(session, user_id)
    include_tech = bool(getattr(user_settings, "include_tech_updates", False))
    include_reports = bool(getattr(user_settings, "include_industry_reports", False))
    user_prompt = (getattr(user_settings, "user_prompt", "") or "").strip()

    # Убрать из дайджеста кластеры, похожие на те, что пользователь отметил «Мимо» (по смыслу, не по типу)
    def _usable_emb(emb):
        if emb is None:
            return False
        try:
            return len(emb) > 0
        except (TypeError, AttributeError):
            return False

    threshold = settings.feedback_dislike_similarity_threshold
    disliked_embeddings = []
    if threshold < 1.0:
        for dc in await get_user_disliked_clusters(session, user_id):
            emb = getattr(dc, "embedding", None) if dc else None
            if not _usable_emb(emb) and dc and getattr(dc, "canonical_summary", None):
                emb = generate_embedding(dc.canonical_summary[:2000])
            if _usable_emb(emb):
                disliked_embeddings.append(emb)
    def _cluster_similar_to_disliked(cluster) -> bool:
        if not cluster or not disliked_embeddings or threshold >= 1.0:
            return False
        c_emb = getattr(cluster, "embedding", None)
        if not _usable_emb(c_emb) and getattr(cluster, "canonical_summary", None):
            c_emb = generate_embedding(cluster.canonical_summary[:2000])
        if not _usable_emb(c_emb):
            return False
        return any(cosine_similarity(c_emb, d) >= threshold for d in disliked_embeddings)
    unique_posts = [p for p in unique_posts if not (p.cluster_id and _cluster_similar_to_disliked(clusters_map.get(p.cluster_id)))]

    def _post_sort_key(post):
        cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
        kind_weight = {"product": 6, "tech_update": 4, "industry_report": 3, "trend": 2, "research": 1, "misc": 0}.get(
            (cluster.news_kind if cluster else "misc"),
            1,
        )
        priority_weight = _priority_rank(cluster.priority if cluster else "low")
        product_score = float(cluster.product_score) if cluster else 0.0
        mentions = int(cluster.mention_count) if cluster else 1
        implementable = 1 if (cluster and cluster.implementable_by_small_team) else 0
        barrier_penalty = 0 if not cluster else {"low": 0.1, "medium": 0.0, "high": -0.2}.get(cluster.infra_barrier, -0.1)
        return (kind_weight, implementable, priority_weight, product_score + barrier_penalty, mentions, post.reactions_count)

    unique_posts.sort(key=_post_sort_key, reverse=True)

    target_items = max(6, settings.digest_target_items)
    product_target = max(1, int(target_items * settings.digest_product_share))
    non_product_cap = max(1, settings.digest_max_non_product)

    product_posts = []
    tech_posts = []
    report_posts = []
    trend_posts = []
    research_posts = []
    tech_posts_fallback = []   # tech_update по типу, без жёсткого отбора по качеству
    report_posts_fallback = []
    for post in unique_posts:
        cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
        kind = cluster.news_kind if cluster else "misc"
        if kind == "tech_update":
            tech_posts_fallback.append(post)
        if kind == "industry_report":
            report_posts_fallback.append(post)
        if not _is_digest_candidate(cluster):
            continue
        if mode == "tech_update" and kind != "tech_update":
            continue
        if mode == "industry_report" and kind != "industry_report":
            continue
        if mode == "main":
            if kind == "tech_update" and not include_tech:
                continue
            if kind == "industry_report" and not include_reports:
                continue
        if kind == "product":
            product_posts.append(post)
        elif kind == "tech_update":
            tech_posts.append(post)
        elif kind == "industry_report":
            report_posts.append(post)
        elif kind == "trend":
            trend_posts.append(post)
        elif kind == "research":
            research_posts.append(post)

    if mode == "tech_update":
        selected_posts = tech_posts[:target_items]
    elif mode == "industry_report":
        selected_posts = report_posts[:target_items]
    else:
        selected_posts = product_posts[:product_target]
        non_product = tech_posts + report_posts + trend_posts + research_posts
        selected_posts.extend(non_product[:non_product_cap])
    if len(selected_posts) < target_items:
        already = {p.id for p in selected_posts}
        for post in unique_posts:
            if post.id in already:
                continue
            cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
            if not _is_digest_candidate(cluster):
                continue
            kind = cluster.news_kind if cluster else "misc"
            if mode == "tech_update" and kind != "tech_update":
                continue
            if mode == "industry_report" and kind != "industry_report":
                continue
            if mode == "main":
                if kind == "tech_update" and not include_tech:
                    continue
                if kind == "industry_report" and not include_reports:
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
        analogs_text = cluster.analogs if cluster and cluster.analogs else ""
        action_item = cluster.action_item if cluster and cluster.action_item else ""
        news_kind = cluster.news_kind if cluster else "misc"
        product_score = float(cluster.product_score) if cluster else 0.0
        coreai_score = float(cluster.coreai_score) if cluster else 0.0
        if not action_item and news_kind == "product":
            action_item = "Снять фичу на декомпозицию: value, UX, метрики, срок пилота."

        user_relevance_score = await score_user_prompt_relevance(post_text, user_prompt) if user_prompt else 0.5
        if user_prompt and user_relevance_score < settings.user_prompt_min_score and mode == "main":
            continue

        summaries.append({
            "source": source_title,
            "summary": post_text,
            "reactions": post.reactions_count,
            "link": link,
            "mentions": mentions,
            "tags": tags_text,
            "analogs": analogs_text,
            "action_item": action_item,
            "news_kind": news_kind,
            "product_score": product_score,
            "coreai_score": coreai_score,
            "user_relevance_score": user_relevance_score,
        })

    fallback_used = False
    if not summaries and mode == "tech_update" and tech_posts_fallback:
        for post in tech_posts_fallback[:5]:
            post_text = post.summary or post.content[:300]
            if not _is_ai(post_text):
                continue
            source = await get_source_by_id(session, post.source_id)
            source_title = source.title or source.identifier if source else "Неизвестный"
            link = _get_post_link(source, post)
            cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
            mentions = cluster.mention_count if cluster else 1
            tags_text = " ".join(tag for tag in (cluster.tags or "").split(",") if tag) if cluster else "#AIТехнологии"
            analogs_text = cluster.analogs if cluster and cluster.analogs else ""
            action_item = cluster.action_item if cluster and cluster.action_item else ""
            news_kind = cluster.news_kind if cluster else "misc"
            product_score = float(cluster.product_score) if cluster else 0.0
            coreai_score = float(cluster.coreai_score) if cluster else 0.0
            if not action_item and news_kind == "product":
                action_item = "Снять фичу на декомпозицию: value, UX, метрики, срок пилота."
            user_relevance_score = await score_user_prompt_relevance(post_text, user_prompt) if user_prompt else 0.5
            summaries.append({
                "source": source_title,
                "summary": post_text,
                "reactions": post.reactions_count,
                "link": link,
                "mentions": mentions,
                "tags": tags_text,
                "analogs": analogs_text,
                "action_item": action_item,
                "news_kind": news_kind,
                "product_score": product_score,
                "coreai_score": coreai_score,
                "user_relevance_score": user_relevance_score,
            })
        if summaries:
            fallback_used = True
    if not summaries and mode == "industry_report" and report_posts_fallback:
        for post in report_posts_fallback[:5]:
            post_text = post.summary or post.content[:300]
            if not _is_ai(post_text):
                continue
            source = await get_source_by_id(session, post.source_id)
            source_title = source.title or source.identifier if source else "Неизвестный"
            link = _get_post_link(source, post)
            cluster = clusters_map.get(post.cluster_id) if post.cluster_id else None
            mentions = cluster.mention_count if cluster else 1
            tags_text = " ".join(tag for tag in (cluster.tags or "").split(",") if tag) if cluster else "#AIТехнологии"
            analogs_text = cluster.analogs if cluster and cluster.analogs else ""
            action_item = cluster.action_item if cluster and cluster.action_item else ""
            news_kind = cluster.news_kind if cluster else "misc"
            product_score = float(cluster.product_score) if cluster else 0.0
            coreai_score = float(cluster.coreai_score) if cluster else 0.0
            if not action_item and news_kind == "product":
                action_item = "Снять фичу на декомпозицию: value, UX, метрики, срок пилота."
            user_relevance_score = await score_user_prompt_relevance(post_text, user_prompt) if user_prompt else 0.5
            summaries.append({
                "source": source_title,
                "summary": post_text,
                "reactions": post.reactions_count,
                "link": link,
                "mentions": mentions,
                "tags": tags_text,
                "analogs": analogs_text,
                "action_item": action_item,
                "news_kind": news_kind,
                "product_score": product_score,
                "coreai_score": coreai_score,
                "user_relevance_score": user_relevance_score,
            })
        if summaries:
            fallback_used = True

    if not summaries:
        mode_human = {
            "main": "продуктовых",
            "tech_update": "технологических обновлений",
            "industry_report": "отчётов и аналитики",
        }.get(mode, "релевантных")
        return (
            "📰 <b>Дайджест за сегодня</b>\n\n"
            f"Пока нет релевантных {mode_human} AI/LLM-новостей высокого качества. "
            "Следующий цикл парсинга добавит новые кандидаты."
        )

    digest_fallback_note = ""
    if fallback_used:
        digest_fallback_note = (
            "Релевантных технологических обновлений высокого качества не нашлось. "
            "Ниже — подборка по теме за день.\n\n"
        ) if mode == "tech_update" else (
            "Релевантных отчётов и аналитики высокого качества не нашлось. "
            "Ниже — подборка по теме за день.\n\n"
        ) if mode == "industry_report" else ""

    # Generate digest via LLM
    digest_text = await generate_digest_text(summaries)

    if digest_text:
        digest_text = digest_fallback_note + _inject_curated_links_inline(digest_text, summaries)
        business_block = await _build_digest_business_impact_block(summaries)

        # Deterministic per-news section with inline tags (LLM output may reorder/omit markers).
        per_news_section = "\n\n🧷 <b>Новости по источникам:</b>\n"
        for i, s in enumerate(summaries[:10], 1):
            mentions_text = f" | 📈 {s['mentions']} источн." if s.get("mentions", 1) >= 2 else ""
            short_summary = _trim_text(s["summary"] or "", 120)
            link_text = f'\n🔗 <a href="{s["link"]}">Оригинал</a>' if s.get("link") else ""
            per_news_section += (
                f'{i}. <b>{s["source"]}</b>\n'
                f'🏷 {s.get("tags") or "#AIТехнологии"}{mentions_text}\n'
                f'{short_summary}\n'
                f'🧩 Аналоги: {s.get("analogs") or "нет явных"}\n'
                f'✅ Action: {s.get("action_item") or "Нет явного продуктового action"}'
                f'{link_text}\n\n'
            )
        digest_text += business_block + per_news_section
    else:
        # Fallback: simple list with links, HTML format
        digest_text = digest_fallback_note + "📰 <b>Дайджест за сегодня:</b>\n\n"
        for i, s in enumerate(summaries[:10], 1):
            link_text = f'\n🔗 <a href="{s["link"]}">Оригинал</a>' if s["link"] else ""
            mentions_text = f", {s['mentions']} источн." if s.get("mentions", 1) >= 2 else ""
            digest_text += f'{i}. <b>{s["source"]}</b> (👍 {s["reactions"]}{mentions_text})\n{s["summary"]}{link_text}\n\n'

    return digest_text


async def _build_digest_business_impact_block(summaries: list[dict]) -> str:
    if not settings.tavily_api_key or not summaries:
        return ""

    quality_candidates = [
        s for s in summaries
        if (
            s.get("news_kind") == "product" and float(s.get("product_score", 0.0)) >= 0.6
        ) or (
            s.get("news_kind") in {"trend", "research"} and float(s.get("coreai_score", 0.0)) >= 0.8
        )
    ]
    candidates = sorted(
        quality_candidates,
        key=lambda s: (s.get("product_score", 0.0), s.get("coreai_score", 0.0), s.get("mentions", 1)),
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
