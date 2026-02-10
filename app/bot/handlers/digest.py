import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import back_to_menu_keyboard
from app.db.models import User
from app.db.repositories import get_user_sources
from app.services.digest import generate_digest_for_user

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "menu:digest")
async def show_digest(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text("⏳ Формирую дайджест за сегодня...")

    try:
        digest_text = await generate_digest_for_user(session, user.id)
        if digest_text:
            # Telegram has a 4096 char limit per message
            if len(digest_text) > 4000:
                chunks = [digest_text[i:i + 4000] for i in range(0, len(digest_text), 4000)]
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        await callback.message.answer(
                            chunk,
                            reply_markup=back_to_menu_keyboard(),
                            parse_mode="Markdown",
                        )
                    else:
                        await callback.message.answer(chunk, parse_mode="Markdown")
                # Delete the "loading" message
                try:
                    await callback.message.delete()
                except Exception:
                    pass
            else:
                await callback.message.edit_text(
                    digest_text,
                    reply_markup=back_to_menu_keyboard(),
                    parse_mode="Markdown",
                )
        else:
            await callback.message.edit_text(
                "📭 За сегодня новостей пока нет.\n\n"
                "Убедитесь, что вы подписаны на каналы и ссылки.",
                reply_markup=back_to_menu_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error generating digest: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при формировании дайджеста. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard(),
        )
