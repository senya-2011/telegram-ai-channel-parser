import logging
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.functions.messages import GetMessagesReactionsRequest

from app.config import settings
from app.db.database import async_session
from app.db.repositories import create_post, get_all_sources, get_existing_external_ids

logger = logging.getLogger(__name__)

_client: TelegramClient | None = None


async def get_telethon_client() -> TelegramClient:
    """Get or create the Telethon client."""
    global _client
    if _client is None or not _client.is_connected():
        _client = TelegramClient(
            "tg_parser_session",
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await _client.start(phone=settings.telegram_phone)
        logger.info("Telethon client connected")
    return _client


async def disconnect_telethon():
    """Disconnect the Telethon client."""
    global _client
    if _client and _client.is_connected():
        await _client.disconnect()
        _client = None


def _count_reactions(message) -> int:
    """Count total reactions on a message."""
    total = 0
    if hasattr(message, "reactions") and message.reactions:
        if hasattr(message.reactions, "results"):
            for reaction in message.reactions.results:
                total += reaction.count
    return total


async def parse_telegram_channels():
    """Parse all registered Telegram channels for new posts."""
    logger.info("Starting Telegram channels parsing...")

    try:
        client = await get_telethon_client()
    except Exception as e:
        logger.error(f"Failed to connect Telethon client: {e}")
        return

    async with async_session() as session:
        sources = await get_all_sources(session, source_type="telegram")

        for source in sources:
            channel_username = source.identifier.lstrip("@")
            try:
                await _parse_single_channel(client, session, source.id, channel_username)
            except Exception as e:
                logger.error(f"Error parsing channel @{channel_username}: {e}")


async def _parse_single_channel(
    client: TelegramClient,
    session,
    source_id: int,
    channel_username: str,
    limit: int = 20,
):
    """Parse a single Telegram channel for recent messages."""
    logger.info(f"Parsing channel @{channel_username}...")

    try:
        entity = await client.get_entity(channel_username)
    except Exception as e:
        logger.error(f"Cannot find channel @{channel_username}: {e}")
        return

    messages = await client.get_messages(entity, limit=limit)
    new_count = 0
    external_ids = [str(msg.id) for msg in messages if msg.text and len(msg.text.strip()) >= 30]
    existing_ids = await get_existing_external_ids(session, source_id=source_id, external_ids=external_ids)

    for msg in messages:
        if not msg.text or len(msg.text.strip()) < 30:
            # Skip very short messages (likely media-only, stickers, etc.)
            continue

        external_id = str(msg.id)
        if external_id in existing_ids:
            continue
        reactions_count = _count_reactions(msg)

        published_at = msg.date
        if published_at and published_at.tzinfo:
            published_at = published_at.replace(tzinfo=None)

        post = await create_post(
            session=session,
            source_id=source_id,
            external_id=external_id,
            content=msg.text,
            reactions_count=reactions_count,
            published_at=published_at,
            commit=False,
        )

        if post:
            new_count += 1
            logger.debug(f"New post from @{channel_username}: {msg.text[:60]}...")

    if new_count > 0:
        await session.commit()
        logger.info(f"Parsed {new_count} new posts from @{channel_username}")
    else:
        logger.debug(f"No new posts from @{channel_username}")
