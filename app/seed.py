"""
Seed script: populate the database with default channels and sources.
Run once after migrations:
    python -m app.seed
"""
import asyncio
import logging

from sqlalchemy import text

from app.db.database import async_session
from app.db.repositories import get_or_create_source

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default Telegram channels — AI / нейросети / технологии
DEFAULT_TELEGRAM_CHANNELS = [
    # Дополнительные AI-каналы
    ("@openai_ru", "OpenAI на русском"),
    ("@deeplearning_ru", "Deep Learning Russia"),
    ("@huggingface_ru", "Hugging Face RU"),
    ("@ai_machinelearning", "AI & Machine Learning"),
    ("@gonzo_ML", "Gonzo ML"),
    ("@denissexy", "Denis Sexy IT — AI/ML"),
]

# Default web sources — AI / технологии
DEFAULT_WEB_SOURCES = [
    ("https://habr.com/ru/flows/develop/", "Habr — Разработка"),
    ("https://the-decoder.com/", "The Decoder — AI News"),
    ("https://openai.com/blog", "OpenAI Blog"),
    ("https://deepmind.google/blog/", "Google DeepMind Blog"),
    ("https://huggingface.co/blog", "Hugging Face Blog"),
]

# Default API sources — AI-focused feeds with strong signal
DEFAULT_REDDIT_SOURCES = [
    ("MachineLearning", "Reddit r/MachineLearning"),
    ("LocalLLaMA", "Reddit r/LocalLLaMA"),
    ("artificial", "Reddit r/artificial"),
    ("OpenAI", "Reddit r/OpenAI"),
    ("ChatGPT", "Reddit r/ChatGPT"),
    ("singularity", "Reddit r/singularity"),
    ("StableDiffusion", "Reddit r/StableDiffusion"),
]

DEFAULT_GITHUB_SOURCES = [
    ("openai/openai-python", "GitHub OpenAI Python"),
    ("huggingface/transformers", "GitHub Hugging Face Transformers"),
    ("langchain-ai/langchain", "GitHub LangChain"),
    ("openai/openai-cookbook", "GitHub OpenAI Cookbook"),
    ("vllm-project/vllm", "GitHub vLLM"),
    ("ollama/ollama", "GitHub Ollama"),
    ("microsoft/autogen", "GitHub AutoGen"),
    ("crewAIInc/crewAI", "GitHub CrewAI"),
]

DEFAULT_PRODUCTHUNT_SOURCES = [
    ("ai", "Product Hunt — AI"),
    ("llm", "Product Hunt — LLM"),
    ("automation", "Product Hunt — Automation"),
    ("developer", "Product Hunt — Developer Tools"),
]


async def seed_defaults():
    """Create default sources in the database."""
    logger.info("Seeding default sources...")

    async with async_session() as session:
        for identifier, title in DEFAULT_TELEGRAM_CHANNELS:
            source = await get_or_create_source(
                session,
                source_type="telegram",
                identifier=identifier,
                title=title,
                is_default=True,
            )
            logger.info(f"  [telegram] {identifier} -> id={source.id}")

        for url, title in DEFAULT_WEB_SOURCES:
            source = await get_or_create_source(
                session,
                source_type="web",
                identifier=url,
                title=title,
                is_default=True,
            )
            logger.info(f"  [web] {url} -> id={source.id}")

        for identifier, title in DEFAULT_REDDIT_SOURCES:
            source = await get_or_create_source(
                session,
                source_type="reddit",
                identifier=identifier,
                title=title,
                is_default=True,
            )
            logger.info(f"  [reddit] {identifier} -> id={source.id}")

        for identifier, title in DEFAULT_GITHUB_SOURCES:
            source = await get_or_create_source(
                session,
                source_type="github",
                identifier=identifier,
                title=title,
                is_default=True,
            )
            logger.info(f"  [github] {identifier} -> id={source.id}")

        for identifier, title in DEFAULT_PRODUCTHUNT_SOURCES:
            source = await get_or_create_source(
                session,
                source_type="producthunt",
                identifier=identifier,
                title=title,
                is_default=True,
            )
            logger.info(f"  [producthunt] {identifier} -> id={source.id}")

        # Ensure all existing users are subscribed to all default sources.
        # Safe to rerun due to ON CONFLICT DO NOTHING.
        result = await session.execute(
            text(
                """
                INSERT INTO user_sources (user_id, source_id)
                SELECT u.id, s.id
                FROM users u
                CROSS JOIN sources s
                WHERE s.is_default = true
                ON CONFLICT (user_id, source_id) DO NOTHING
                """
            )
        )
        await session.commit()
        logger.info(f"  [subscriptions] added default subscriptions for existing users: {result.rowcount}")

    logger.info("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed_defaults())
