from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.db.database import async_session
from app.db.repositories import get_user_by_telegram_id


class AuthMiddleware(BaseMiddleware):
    """
    Middleware that injects `db_session` and checks user authentication.
    Passes `user` in handler data if authenticated.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Always provide a DB session
        async with async_session() as session:
            data["session"] = session

            # Get telegram user id
            tg_user_id = None
            if isinstance(event, Message) and event.from_user:
                tg_user_id = event.from_user.id
            elif isinstance(event, CallbackQuery) and event.from_user:
                tg_user_id = event.from_user.id

            if tg_user_id:
                user = await get_user_by_telegram_id(session, tg_user_id)
                data["user"] = user
            else:
                data["user"] = None

            return await handler(event, data)
