import asyncio
import logging
import sys

from app.bot.bot import create_bot, create_dispatcher
from app.scheduler.tasks import setup_scheduler
from app.services.telegram_parser import disconnect_telethon

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

# Reduce noise from libraries
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def main():
    bot = create_bot()
    dp = create_dispatcher()
    scheduler = setup_scheduler(bot)

    @dp.startup()
    async def on_startup():
        logger.info("Bot is starting up...")

        # Set bot commands (visible in Telegram menu button)
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="start", description="Начало работы"),
            BotCommand(command="menu", description="Открыть меню"),
            BotCommand(command="status", description="Статистика парсинга"),
            BotCommand(command="help", description="Справка"),
        ])

        scheduler.start()
        logger.info("Scheduler started")
        # Preload embedding model
        try:
            from app.services.embedding import _get_model
            _get_model()
        except Exception as e:
            logger.warning(f"Failed to preload embedding model: {e}")

        # Run first parse immediately on startup
        logger.info("Running initial parse...")
        from app.scheduler.tasks import task_parse_telegram, task_parse_web
        asyncio.create_task(task_parse_telegram(bot))
        asyncio.create_task(task_parse_web(bot))

        logger.info("Bot startup complete!")

    @dp.shutdown()
    async def on_shutdown():
        logger.info("Bot is shutting down...")
        scheduler.shutdown(wait=False)
        await disconnect_telethon()
        logger.info("Bot shutdown complete.")

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
