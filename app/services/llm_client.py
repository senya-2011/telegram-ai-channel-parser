import logging
import json
import re
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

NEWS_TAGS = [
    "#AIТехнологии",
    "#LLM",
    "#GPT",
    "#Агенты",
    "#OpenSourceAI",
    "#РелизМодели",
    "#ИнвестицииAI",
    "#РегулированиеAI",
    "#БезопасностьAI",
    "#Робототехника",
]


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


async def summarize_post(content: str) -> Optional[str]:
    """Generate a concise summary of a post/article using DeepSeek."""
    client = get_llm_client()

    # Truncate very long content
    if len(content) > 4000:
        content = content[:4000] + "..."

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — новостной аналитик. Твоя задача — кратко изложить суть новости "
                        "в 2-3 предложениях на русском языке. Будь конкретным, выделяй ключевые факты. "
                        "Не добавляй свои комментарии или оценки."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Сделай краткую выжимку этой новости:\n\n{content}",
                },
            ],
            max_tokens=300,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip()
        logger.debug(f"Summary generated: {summary[:80]}...")
        return summary
    except Exception as e:
        logger.error(f"LLM summarization error: {e}")
        return None


async def analyze_post(content: str) -> dict:
    """
    Single LLM call for summary + AI relevance + CoreAI importance.
    Returns:
      {
        "summary": str,
        "is_relevant": bool,
        "coreai_score": float (0..1),
        "coreai_reason": str,
        "tags": list[str],
        "news_kind": str,
        "implementable_by_small_team": bool,
        "infra_barrier": str,
        "product_score": float,
        "priority": str,
        "is_alert_worthy": bool,
        "analogs": list[str],
        "action_item": str,
      }
    """
    client = get_llm_client()

    if len(content) > 4000:
        content = content[:4000] + "..."

    prompt = (
        "Ты продуктовый AI-аналитик для команды CoreAI.\n"
        "Твоя цель: отбирать новости, которые помогают создавать и улучшать AI/LLM-продукты.\n"
        "ФОКУС: реальные кейсы успеха продуктов (трафик, выручка, экзит, внедрение с цифрами). Анонсы вендоров (модели, API, агенты) — это технологии, не product.\n\n"
        "Верни ответ СТРОГО в JSON с полями:\n"
        "summary: string (2-3 предложения на русском, только факты)\n"
        "is_relevant: boolean\n"
        "coreai_score: number от 0 до 1\n"
        "coreai_reason: string (1-2 предложения, почему важно/не важно для CoreAI)\n"
        "analogs: array<string> (0-3 названия конкурентов/аналогов, которых стоит посмотреть)\n"
        "action_item: string (одно конкретное действие для CoreAI, формулировка в повелительном стиле)\n"
        "tags: array<string> из 1-3 хештегов\n"
        "news_kind: string из {product, trend, research, tech_update, industry_report, misc}\n"
        "implementable_by_small_team: boolean\n"
        "infra_barrier: string из {low, medium, high}\n"
        "product_score: number от 0 до 1\n"
        "priority: string из {high, medium, low}\n"
        "is_alert_worthy: boolean\n\n"
        f"Используй только эти хештеги: {', '.join(NEWS_TAGS)}\n\n"
        "Правила классификации (строго соблюдай):\n"
        "- product: ТОЛЬКО кейсы успеха с цифрами — кто-то создал продукт/сервис и есть доказанный результат. Примеры: «стартап за неделю набрал N пользователей», «сервис купили (Google/другая компания)», «внедрили и сэкономили X% / Y млн», «выручка Z», «N компаний уже используют». Без цифр успеха/трафика/экзита — НЕ product. Любой анонс вида «компания X выпустила модель/агента/API» — это tech_update, даже если это «запустили в preview» или «доступен подписчикам».\n"
        "- tech_update: всё, что «вышло» или «можно юзать», без истории успеха продукта. ВСЕГДА сюда: релизы моделей (GLM-4.7, Gemini, GPT, Claude, Llama), анонсы агентов (Claude Cowork и т.д.), новые API, preview от вендоров, новые версии библиотек/SDK, гайды, документация, best practices, licensing/pricing. Формулировки «X выпустила Y», «запустили в preview», «доступен в API» — tech_update, не product.\n"
        "- trend: крупный рыночный сдвиг, который может изменить продуктовую стратегию.\n"
        "- research: результат исследований, который можно применить в продукте в обозримом горизонте.\n"
        "- industry_report: отчеты McKinsey/BCG/Gartner/Deloitte/фондов с цифрами и практическими выводами.\n"
        "- misc: все остальное.\n\n"
        "Критерии implementable_by_small_team=true:\n"
        "- решение можно повторить командой 3-7 человек за 2-8 недель;\n"
        "- не требуется обучение frontier-моделей, собственные дата-центры и многомиллионный capex;\n"
        "- опора на доступные API/opensource/стандартный cloud stack.\n\n"
        "Критерии infra_barrier:\n"
        "- low: можно собрать из готовых API/инструментов;\n"
        "- medium: требуется сложная интеграция и продвинутая MLOps;\n"
        "- high: нужна тяжелая инфраструктура, уникальные данные, крупный капитал.\n\n"
        "Критерии high priority:\n"
        "- для product: только при наличии цифр успеха (пользователи, выручка, экзит, кейс с метриками);\n"
        "- подтвержденные кейсы внедрения с влиянием на бизнес-метрики (выручка, конверсия, CAC, удержание, cost/time savings);\n"
        "- изменения платформ/регуляторики, которые прямо влияют на roadmap CoreAI.\n"
        "- НЕ high priority: «компания X внедрила AI» без цифр; жалобы пользователей на тон/поведение бота; рутинный релиз модели (MiniMax, Llama и т.д.) — это tech_update с priority medium/low.\n\n"
        "Критерии low priority:\n"
        "- абстрактные рассуждения без данных;\n"
        "- реклама, вакансии, курсы, общие IT-статьи без AI-продуктового угла;\n"
        "- локальные новости без масштабируемого урока.\n\n"
        "ЧТО НЕ СЧИТАТЬ PRODUCT И НЕ АЛЕРТИТЬ (строго):\n"
        "- «Компания X говорит, что их разработчики не пишут код благодаря AI» / «внедрили внутренние AI-решения» без цифр (экономия %, сроки, объём) — это не product, это внутренний PR. Классифицируй как misc или trend, priority=low, is_alert_worthy=false.\n"
        "- Жалобы пользователей на тон/поведение ChatGPT/другого ассистента («конденсцендирующие ответы», «анализируют мотивы») — это не product, не кейс успеха. Классифицируй как misc, priority=low, is_alert_worthy=false.\n"
        "- Релиз модели с открытыми весами (MiniMax M2.5, Llama, GLM и т.д.) — всегда tech_update, не product. priority для tech_update может быть high только при реально критичном изменении рынка; иначе medium/low. Не раздувай coreai_score и is_alert_worthy для рутинных релизов.\n"
        "- Любое «абстрактное нечто», которое невозможно реализовать или проверить (общие рассуждения, чужие мнения без кейса, новости без конкретного action_item) — misc, priority=low, is_alert_worthy=false.\n\n"
        "Важно: (1) «Компания X выпустила/запустила модель/агента/API» — всегда tech_update, не product. (2) product = только когда в новости есть история успеха: цифры пользователей, выручка, экзит, кейс внедрения с метриками. Без этого — tech_update или misc. (3) В «Важную новость» для CoreAI только реально важное: кейсы успеха продукта с цифрами или критичные изменения платформ/регуляторики. Не постить внутренние PR, жалобы пользователей, рутинные релизы моделей.\n\n"
        "Правило для is_alert_worthy=true:\n"
        "- только если новость high priority ИЛИ coreai_score >= 0.78;\n"
        "- для product: только при наличии цифр успеха (пользователи, выручка, экзит, кейс с метриками). Без цифр — is_alert_worthy=false.\n"
        "- для tech_update: true только при действительно критичном релизе/изменении, не для каждого анонса модели.\n"
        "- внутренний PR («компания внедрила AI» без метрик), жалобы пользователей, абстрактные рассуждения — всегда is_alert_worthy=false.\n"
        "- у новости должен быть четкий, реализуемый вывод для продуктового решения CoreAI.\n\n"
        "Правило для analogs:\n"
        "- включай только реально релевантные продукты/компании;\n"
        "- не более 3;\n"
        "- если аналогов нет, верни пустой массив.\n\n"
        "Правило для action_item:\n"
        "- одно действие, которое можно выполнить за 1-2 недели;\n"
        "- конкретно и проверяемо (например: сравнить X с нашим Y и запустить пилот на Z).\n\n"
        "Никакого markdown. Никакого текста вне JSON."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=450,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        data = _extract_json_object(raw)

        summary = str(data.get("summary", "")).strip()
        if not summary:
            summary = content[:300] + "..." if len(content) > 300 else content

        raw_relevant = data.get("is_relevant", False)
        if isinstance(raw_relevant, bool):
            is_relevant = raw_relevant
        else:
            is_relevant = str(raw_relevant).strip().lower() in {"true", "yes", "1"}
        try:
            coreai_score = float(data.get("coreai_score", 0.0))
        except Exception:
            coreai_score = 0.0
        coreai_score = max(0.0, min(1.0, coreai_score))

        coreai_reason = str(data.get("coreai_reason", "")).strip()
        news_kind = str(data.get("news_kind", "misc")).strip().lower()
        if news_kind not in {"product", "trend", "research", "tech_update", "industry_report", "misc"}:
            news_kind = "misc"
        raw_impl = data.get("implementable_by_small_team", False)
        if isinstance(raw_impl, bool):
            implementable_by_small_team = raw_impl
        else:
            implementable_by_small_team = str(raw_impl).strip().lower() in {"true", "yes", "1"}
        infra_barrier = str(data.get("infra_barrier", "high")).strip().lower()
        if infra_barrier not in {"low", "medium", "high"}:
            infra_barrier = "high"
        try:
            product_score = float(data.get("product_score", 0.0))
        except Exception:
            product_score = 0.0
        product_score = max(0.0, min(1.0, product_score))
        priority = str(data.get("priority", "low")).strip().lower()
        if priority not in {"high", "medium", "low"}:
            priority = "low"
        raw_alert = data.get("is_alert_worthy", False)
        if isinstance(raw_alert, bool):
            is_alert_worthy = raw_alert
        else:
            is_alert_worthy = str(raw_alert).strip().lower() in {"true", "yes", "1"}
        raw_analogs = data.get("analogs", [])
        if isinstance(raw_analogs, str):
            raw_analogs = [a.strip() for a in raw_analogs.split(",") if a.strip()]
        elif not isinstance(raw_analogs, list):
            raw_analogs = []
        analogs = [str(a).strip() for a in raw_analogs if str(a).strip()][:3]
        action_item = str(data.get("action_item", "")).strip()
        if not action_item and news_kind == "product":
            action_item = "Сравнить фичу с нашим roadmap и запланировать эксперимент."
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        elif not isinstance(raw_tags, list):
            raw_tags = []
        allowed = set(NEWS_TAGS)
        tags = [tag for tag in raw_tags if isinstance(tag, str) and tag in allowed]
        if not tags and is_relevant:
            tags = ["#AIТехнологии"]
        tags = tags[:3]
        return {
            "summary": summary,
            "is_relevant": is_relevant,
            "coreai_score": coreai_score,
            "coreai_reason": coreai_reason,
            "tags": tags,
            "news_kind": news_kind,
            "implementable_by_small_team": implementable_by_small_team,
            "infra_barrier": infra_barrier,
            "product_score": product_score,
            "priority": priority,
            "is_alert_worthy": is_alert_worthy,
            "analogs": analogs,
            "action_item": action_item,
        }
    except Exception as e:
        logger.error(f"LLM combined analysis error: {e}")
        fallback_summary = content[:300] + "..." if len(content) > 300 else content
        return {
            "summary": fallback_summary,
            "is_relevant": True,
            "coreai_score": 0.0,
            "coreai_reason": "LLM error fallback",
            "tags": ["#AIТехнологии"],
            "news_kind": "misc",
            "implementable_by_small_team": False,
            "infra_barrier": "high",
            "product_score": 0.0,
            "priority": "low",
            "is_alert_worthy": False,
            "analogs": [],
            "action_item": "",
        }


async def score_user_prompt_relevance(summary: str, user_prompt: str) -> float:
    """Score how relevant a news summary is to the user custom prompt (0..1)."""
    if not user_prompt or len(user_prompt.strip()) < 5:
        return 0.5

    client = get_llm_client()
    prompt = (
        "Ты фильтр персонализации новостей.\n"
        "Оцени соответствие новости пользовательскому фильтру.\n"
        "Верни СТРОГО JSON: {\"user_relevance_score\": number}\n"
        "Где score от 0 до 1.\n"
        "0 = не соответствует, 1 = полностью соответствует.\n"
        "Без markdown и лишнего текста."
    )
    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Пользовательский фильтр:\n{user_prompt[:1200]}\n\nНовость:\n{summary[:1500]}",
                },
            ],
            max_tokens=80,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        data = _extract_json_object(raw)
        score = float(data.get("user_relevance_score", 0.5))
        return max(0.0, min(1.0, score))
    except Exception as e:
        logger.debug(f"User relevance scoring error: {e}")
        return 0.5


async def check_ai_relevance(text: str) -> bool:
    """
    Check if a post is a real AI/ML/tech news (not an ad, promo, or off-topic).
    Returns True if the post is AI-relevant news, False otherwise.
    """
    client = get_llm_client()

    # Truncate to keep it cheap and fast
    text = text[:500]

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — фильтр новостей. Определи, является ли текст РЕАЛЬНОЙ новостью или статьёй "
                        "про искусственный интеллект, машинное обучение, нейросети, LLM, GPT, "
                        "автоматизацию с помощью ИИ или связанные технологии.\n\n"
                        "Ответь СТРОГО одним словом:\n"
                        "YES — если это настоящая новость/статья про ИИ/ML/технологии\n"
                        "NO — если это реклама, промо, продажа курсов, подписка на платный контент, "
                        "личное мнение без новости, спам, или тема НЕ связана с ИИ\n\n"
                        "Примеры NO: продажа курсов, предложение подписки, розыгрыш, "
                        "промокод, партнёрская ссылка, набор на вебинар, вакансия."
                    ),
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            max_tokens=5,
            temperature=0,
        )
        answer = response.choices[0].message.content.strip().upper()
        is_relevant = answer.startswith("YES")
        logger.debug(f"AI relevance check: '{text[:60]}...' -> {answer} ({is_relevant})")
        return is_relevant
    except Exception as e:
        logger.error(f"AI relevance check error: {e}")
        # Default to True on error — don't lose real news
        return True


async def check_similarity(post1_summary: str, post2_summary: str) -> dict:
    """
    Ask LLM to confirm whether two posts are about the same news event.
    Returns {"is_similar": bool, "explanation": str}
    """
    client = get_llm_client()

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты сравниваешь две новости. Определи, описывают ли они ОДНО И ТО ЖЕ событие. "
                        "Ответь строго в формате:\n"
                        "SIMILAR: да/нет\n"
                        "ПРИЧИНА: <краткое объяснение на русском>\n"
                        "Не добавляй ничего лишнего."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Новость 1:\n{post1_summary}\n\n"
                        f"Новость 2:\n{post2_summary}\n\n"
                        "Это одна и та же новость?"
                    ),
                },
            ],
            max_tokens=200,
            temperature=0.1,
        )
        text = response.choices[0].message.content.strip()

        is_similar = "да" in text.lower().split("\n")[0]
        # Extract explanation
        explanation = ""
        for line in text.split("\n"):
            if line.upper().startswith("ПРИЧИНА:"):
                explanation = line.split(":", 1)[1].strip()
                break

        if not explanation:
            explanation = text

        return {"is_similar": is_similar, "explanation": explanation}

    except Exception as e:
        logger.error(f"LLM similarity check error: {e}")
        return {"is_similar": False, "explanation": "Ошибка анализа"}


async def generate_digest_text(summaries: list[dict]) -> Optional[str]:
    """
    Generate a formatted daily digest from a list of post summaries.
    summaries: list of {"source": str, "summary": str, "reactions": int, "tags": str, "mentions": int}
    """
    client = get_llm_client()

    if not summaries:
        return None

    posts_text = "\n\n".join(
        f"[{s['source']}] (реакций: {s['reactions']}, источников: {s.get('mentions', 1)})\n"
        f"Теги: {s.get('tags') or '#AIТехнологии'}\n"
        f"{s['summary']}"
        for s in summaries
    )

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — редактор новостного дайджеста для Telegram. "
                        "Составь краткий дайджест на основе новостей. Группируй похожие вместе.\n"
                        "ФОКУС: преимущественно продуктовые новости про AI/LLM (релизы, обновления, продуктовые фичи, API).\n"
                        "Тренды и исследования включай ограниченно, только если они действительно значимы.\n\n"
                        "СТРОГИЕ ПРАВИЛА ФОРМАТИРОВАНИЯ:\n"
                        "- Используй ТОЛЬКО эти HTML-теги: <b>жирный</b>, <i>курсив</i>\n"
                        "- Для списков используй тире: - текст\n"
                        "- Для каждой новости обязательно добавь строку: Теги: #tag1 #tag2\n"
                        "- НИКОГДА не используй * или ** для форматирования\n"
                        "- НИКОГДА не используй Markdown-синтаксис\n\n"
                        "Формат ответа:\n"
                        "📰 <b>Дайджест за сегодня</b>\n\n"
                        "🔥 <b>Главное:</b>\n"
                        "- <b>Заголовок.</b> Описание новости в 1-2 предложения.\n\n"
                        "📌 <b>Также интересно:</b>\n"
                        "- <b>Заголовок.</b> Описание.\n\n"
                        "Пиши на русском языке, кратко и по делу."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Вот новости за сегодня:\n\n{posts_text}",
                },
            ],
            max_tokens=1100,
            temperature=0.5,
        )
        digest = response.choices[0].message.content.strip()
        return digest
    except Exception as e:
        logger.error(f"LLM digest generation error: {e}")
        return None


async def analyze_business_impact(summary: str, contexts: list[dict]) -> dict:
    """
    Analyze real business impact with positive and negative precedents.
    contexts: list of {"title": str, "snippet": str, "url": str}
    Returns:
      {
        "impact_score": float 0..1,
        "positive_precedents": list[str],
        "negative_precedents": list[str],
        "conclusion": str,
      }
    """
    client = get_llm_client()

    context_text = "\n\n".join(
        f"[{c.get('title', 'source')}]\n{c.get('snippet', '')}\nURL: {c.get('url', '')}"
        for c in contexts[:8]
    )

    prompt = (
        "Ты аналитик влияния AI-новостей на реальный бизнес.\n"
        "Нужно оценить практический эффект новости на компании, команды и пользователей.\n"
        "Верни СТРОГО JSON с полями:\n"
        "impact_score: number от 0 до 1\n"
        "positive_precedents: array<string> (1-3 коротких пункта)\n"
        "negative_precedents: array<string> (1-3 коротких пункта)\n"
        "conclusion: string (1-2 предложения)\n"
        "Оценивай выше, если есть конкретные кейсы внедрения, влияние на выручку/издержки/риск.\n"
        "Никакого markdown и текста вне JSON."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"Новость:\n{summary[:1200]}\n\n"
                        f"Контекст из внешних источников:\n{context_text[:5000]}"
                    ),
                },
            ],
            max_tokens=400,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        data = _extract_json_object(raw)

        try:
            impact_score = float(data.get("impact_score", 0.0))
        except Exception:
            impact_score = 0.0
        impact_score = max(0.0, min(1.0, impact_score))

        positive = data.get("positive_precedents", [])
        if not isinstance(positive, list):
            positive = []
        positive = [str(x).strip() for x in positive if str(x).strip()][:3]

        negative = data.get("negative_precedents", [])
        if not isinstance(negative, list):
            negative = []
        negative = [str(x).strip() for x in negative if str(x).strip()][:3]

        conclusion = str(data.get("conclusion", "")).strip()
        return {
            "impact_score": impact_score,
            "positive_precedents": positive,
            "negative_precedents": negative,
            "conclusion": conclusion,
        }
    except Exception as e:
        logger.error(f"LLM business impact analysis error: {e}")
        return {
            "impact_score": 0.0,
            "positive_precedents": [],
            "negative_precedents": [],
            "conclusion": "",
        }
