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
      }
    """
    client = get_llm_client()

    if len(content) > 4000:
        content = content[:4000] + "..."

    prompt = (
        "Ты аналитик CoreAI. Нужно определить ценность новости для мониторинга AI-индустрии.\n"
        "Верни ответ СТРОГО в JSON с полями:\n"
        "summary: string (кратко 2-3 предложения на русском)\n"
        "is_relevant: boolean (true только для реальной новости/статьи по AI/ML/LLM)\n"
        "coreai_score: number от 0 до 1\n"
        "coreai_reason: string (почему это важно/не важно для CoreAI)\n"
        "tags: array<string> из 1-3 хештегов\n"
        f"Используй только эти хештеги: {', '.join(NEWS_TAGS)}\n"
        "Критерии высокой важности для CoreAI:\n"
        "- релиз/обновление модели, продукта или API у ключевых игроков (OpenAI, Anthropic, Google, Meta, xAI, Mistral, DeepSeek)\n"
        "- новые бенчмарки, SOTA-результаты, научные прорывы\n"
        "- крупные инвестиции, M&A, партнерства и регуляторные изменения в AI\n"
        "- инфраструктура/агенты/инструменты, влияющие на разработчиков и бизнес\n"
        "Низкая важность:\n"
        "- реклама курсов, вакансии, промо, личные мнения без фактов\n"
        "- развлекательный контент без новостной ценности\n"
        "Никакого markdown, только валидный JSON."
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
        }


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
                        "Составь краткий дайджест на основе новостей. Группируй похожие вместе.\n\n"
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
            max_tokens=1500,
            temperature=0.5,
        )
        digest = response.choices[0].message.content.strip()
        return digest
    except Exception as e:
        logger.error(f"LLM digest generation error: {e}")
        return None
