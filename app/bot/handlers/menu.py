from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.keyboards import main_menu_keyboard
from app.db.models import User

router = Router()


@router.callback_query(F.data == "menu:main")
async def show_main_menu(callback: CallbackQuery, user: User | None, state: FSMContext):
    await state.clear()
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.message.edit_text(
        f"📋 **Главное меню**\n\nПривет, {user.username}! Выберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, user: User | None, state: FSMContext):
    await state.clear()
    if user:
        await callback.message.edit_text(
            "Действие отменено. Выберите действие:",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await callback.message.edit_text("Действие отменено.")
    await callback.answer()
