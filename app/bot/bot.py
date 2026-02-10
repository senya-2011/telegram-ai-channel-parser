from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import auth, channels, digest, links, menu, settings
from app.bot.middlewares import AuthMiddleware
from app.config import settings as app_settings


def create_bot() -> Bot:
    return Bot(
        token=app_settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # Register middleware
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    # Register routers
    dp.include_router(auth.router)
    dp.include_router(menu.router)
    dp.include_router(channels.router)
    dp.include_router(links.router)
    dp.include_router(digest.router)
    dp.include_router(settings.router)

    return dp
