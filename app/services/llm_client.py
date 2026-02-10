import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


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
    summaries: list of {"source": str, "summary": str, "reactions": int}
    """
    client = get_llm_client()

    if not summaries:
        return None

    posts_text = "\n\n".join(
        f"[{s['source']}] (реакций: {s['reactions']})\n{s['summary']}"
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
