from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import auth_keyboard, main_menu_keyboard
from app.db.models import User

router = Router()


@router.message(Command("menu"))
async def cmd_menu(message: Message, user: User | None, state: FSMContext):
    """Command /menu — open main menu anytime."""
    await state.clear()
    if user:
        await message.answer(
            f"📋 **Главное меню**\n\nПривет, {user.username}! Выберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            "Сначала авторизуйтесь:",
            reply_markup=auth_keyboard(),
        )


@router.message(Command("help"))
async def cmd_help(message: Message, user: User | None):
    """Command /help — show available commands."""
    await message.answer(
        "📖 **Доступные команды:**\n\n"
        "/start — начало работы\n"
        "/menu — открыть главное меню\n"
        "/help — справка по командам",
        parse_mode="Markdown",
    )


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
