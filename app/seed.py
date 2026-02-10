"""
Seed script: populate the database with default channels and sources.
Run once after migrations:
    python -m app.seed
"""
import asyncio
import logging

from app.db.database import async_session
from app.db.repositories import get_or_create_source

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default Telegram channels (popular Russian tech/news channels)
DEFAULT_TELEGRAM_CHANNELS = [
    ("@durov", "Durov's Channel"),
    ("@breakingmash", "Mash"),
    ("@rian_ru", "РИА Новости"),
    ("@tabordigital", "Табор Digital"),
    ("@techcrunch_ru", "TechCrunch RU"),
]

# Default web sources
DEFAULT_WEB_SOURCES = [
    ("https://habr.com/ru/flows/develop/", "Habr - Разработка"),
    ("https://meduza.io/", "Meduza"),
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

    logger.info("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed_defaults())
