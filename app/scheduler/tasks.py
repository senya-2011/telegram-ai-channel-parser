import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.database import async_session
from app.db.repositories import get_all_users_with_settings, get_telegram_ids_for_user, get_user_settings
from app.services.alerts import process_new_posts
from app.services.digest import generate_digest_for_user
from app.services.telegram_parser import parse_telegram_channels
from app.services.web_parser import parse_web_sources

logger = logging.getLogger(__name__)


async def task_parse_telegram(bot: Bot):
    """Scheduled task: parse Telegram channels."""
    logger.info("[Scheduler] Running Telegram parsing task...")
    try:
        await parse_telegram_channels()
    except Exception as e:
        logger.error(f"[Scheduler] Telegram parsing error: {e}")

    # Process new posts (summarize, embed, check alerts)
    try:
        async with async_session() as session:
            await process_new_posts(session, bot)
    except Exception as e:
        logger.error(f"[Scheduler] Post processing error: {e}")


async def task_parse_web(bot: Bot):
    """Scheduled task: parse web sources."""
    logger.info("[Scheduler] Running web parsing task...")
    try:
        await parse_web_sources()
    except Exception as e:
        logger.error(f"[Scheduler] Web parsing error: {e}")

    # Process new posts
    try:
        async with async_session() as session:
            await process_new_posts(session, bot)
    except Exception as e:
        logger.error(f"[Scheduler] Post processing error: {e}")


async def task_send_digests(bot: Bot):
    """
    Scheduled task: check which users should receive their digest now.
    Runs every minute and checks if the current time matches any user's digest_time.
    """
    from datetime import datetime
    import pytz

    logger.debug("[Scheduler] Checking digest schedule...")

    # Step 1: Collect users whose digest time matches now
    users_to_send: list[tuple[int, int, str]] = []  # (user_id, tg_id, username)

    try:
        async with async_session() as session:
            users = await get_all_users_with_settings(session)

            for user in users:
                user_settings = await get_user_settings(session, user.id)
                if not user_settings or not user_settings.digest_time:
                    continue

                try:
                    tz = pytz.timezone(user_settings.timezone or "Europe/Moscow")
                except Exception:
                    tz = pytz.timezone("Europe/Moscow")

                now = datetime.now(tz)
                current_time = now.strftime("%H:%M")

                if current_time == user_settings.digest_time:
                    telegram_ids = await get_telegram_ids_for_user(session, user.id)
                    for tg_id in telegram_ids:
                        users_to_send.append((user.id, tg_id, user.username))
    except Exception as e:
        logger.error(f"[Scheduler] Digest task error (collecting users): {e}")
        return

    if not users_to_send:
        return

    # Step 2: Generate and send digest for each user (separate session per user)
    from app.bot.handlers.digest import _split_text_smart
    from app.bot.keyboards import digest_keyboard

    # Group by user_id to avoid generating digest twice for same user
    from collections import defaultdict
    user_tg_ids: dict[int, list[tuple[int, str]]] = defaultdict(list)  # user_id -> [(tg_id, username)]
    for user_id, tg_id, username in users_to_send:
        user_tg_ids[user_id].append((tg_id, username))

    for user_id, tg_entries in user_tg_ids.items():
        username = tg_entries[0][1]
        logger.info(f"[Scheduler] Generating digest for user {username}")
        try:
            async with async_session() as session:
                digest_text = await generate_digest_for_user(session, user_id)

            if not digest_text:
                logger.info(f"[Scheduler] No digest content for user {username}")
                continue

            keyboard = digest_keyboard()
            chunks = _split_text_smart(digest_text, max_len=4000)

            for tg_id, _ in tg_entries:
                try:
                    for i, chunk in enumerate(chunks):
                        is_last = (i == len(chunks) - 1)
                        try:
                            await bot.send_message(
                                tg_id, chunk,
                                parse_mode="HTML",
                                reply_markup=keyboard if is_last else None,
                            )
                        except Exception:
                            await bot.send_message(
                                tg_id, chunk,
                                reply_markup=keyboard if is_last else None,
                            )
                except Exception as e:
                    logger.error(f"Failed to send digest to tg_id={tg_id}: {e}")

        except Exception as e:
            logger.error(f"Error generating digest for user {username}: {e}")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Create and configure the APScheduler."""
    scheduler = AsyncIOScheduler()

    # Parse Telegram channels every N minutes
    scheduler.add_job(
        task_parse_telegram,
        trigger=IntervalTrigger(minutes=settings.telegram_parse_interval),
        args=[bot],
        id="parse_telegram",
        name="Parse Telegram Channels",
        replace_existing=True,
    )

    # Parse web sources every N minutes
    scheduler.add_job(
        task_parse_web,
        trigger=IntervalTrigger(minutes=settings.web_parse_interval),
        args=[bot],
        id="parse_web",
        name="Parse Web Sources",
        replace_existing=True,
    )

    # Check digest schedule every minute
    scheduler.add_job(
        task_send_digests,
        trigger=IntervalTrigger(minutes=1),
        args=[bot],
        id="send_digests",
        name="Send Digests",
        replace_existing=True,
        max_instances=3,  # allow overlap if generation takes >1 min
    )

    return scheduler
