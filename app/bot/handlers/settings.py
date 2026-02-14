import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import cancel_keyboard, main_menu_keyboard, settings_keyboard
from app.bot.states import SettingsStates
from app.db.models import User
from app.db.repositories import get_user_settings, update_user_settings

router = Router()


@router.callback_query(F.data == "menu:settings")
async def show_settings(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    user_settings = await get_user_settings(session, user.id)
    digest_time = user_settings.digest_time if user_settings else "20:00"
    timezone = user_settings.timezone if user_settings else "Europe/Moscow"
    include_tech_updates = bool(user_settings.include_tech_updates) if user_settings else False
    include_industry_reports = bool(user_settings.include_industry_reports) if user_settings else False
    user_prompt = (user_settings.user_prompt or "").strip() if user_settings else ""

    await callback.message.edit_text(
        f"⚙️ **Настройки**\n\n"
        f"🕐 Время дайджеста: **{digest_time}**\n"
        f"🌍 Часовой пояс: **{timezone}**\n"
        f"🧱 Tech updates в основном дайджесте: **{'ON' if include_tech_updates else 'OFF'}**\n"
        f"📊 Reports в основном дайджесте: **{'ON' if include_industry_reports else 'OFF'}**\n"
        f"🎯 Персональный prompt: **{'задан' if user_prompt else 'не задан'}**",
        reply_markup=settings_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "settings:digest_time")
async def change_digest_time(callback: CallbackQuery, user: User | None, state: FSMContext):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await state.set_state(SettingsStates.waiting_digest_time)
    await callback.message.edit_text(
        "Введите время дайджеста в формате **ЧЧ:ММ** (например, `09:00` или `20:30`):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsStates.waiting_digest_time)
async def process_digest_time(message: Message, user: User | None, state: FSMContext, session: AsyncSession):
    if not user:
        await state.clear()
        return

    time_str = message.text.strip()
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        await message.answer(
            "❌ Некорректный формат. Введите время в формате **ЧЧ:ММ**:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    hours, minutes = map(int, time_str.split(":"))
    if hours > 23 or minutes > 59:
        await message.answer(
            "❌ Некорректное время. Введите корректное время в формате **ЧЧ:ММ**:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    await update_user_settings(session, user.id, digest_time=time_str)
    await state.clear()
    await message.answer(
        f"✅ Время дайджеста обновлено: **{time_str}**\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "settings:timezone")
async def change_timezone(callback: CallbackQuery, user: User | None, state: FSMContext):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await state.set_state(SettingsStates.waiting_timezone)
    await callback.message.edit_text(
        "Введите ваш **часовой пояс** (например, `Europe/Moscow`, `Asia/Almaty`, `UTC`):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsStates.waiting_timezone)
async def process_timezone(message: Message, user: User | None, state: FSMContext, session: AsyncSession):
    if not user:
        await state.clear()
        return

    timezone = message.text.strip()

    # Basic validation
    if "/" not in timezone and timezone != "UTC":
        await message.answer(
            "❌ Некорректный формат. Введите часовой пояс (например, `Europe/Moscow`):",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return

    await update_user_settings(session, user.id, timezone=timezone)
    await state.clear()
    await message.answer(
        f"✅ Часовой пояс обновлён: **{timezone}**\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "settings:toggle_tech_updates")
async def toggle_tech_updates(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return
    current = await get_user_settings(session, user.id)
    new_value = not bool(current.include_tech_updates) if current else True
    await update_user_settings(session, user.id, include_tech_updates=new_value)
    await show_settings(callback, user, session)


@router.callback_query(F.data == "settings:toggle_reports")
async def toggle_reports(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return
    current = await get_user_settings(session, user.id)
    new_value = not bool(current.include_industry_reports) if current else True
    await update_user_settings(session, user.id, include_industry_reports=new_value)
    await show_settings(callback, user, session)


@router.callback_query(F.data == "settings:user_prompt")
async def change_user_prompt(callback: CallbackQuery, user: User | None, state: FSMContext):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_user_prompt)
    await callback.message.edit_text(
        "Введите персональный фильтр (prompt) для ваших алертов/дайджеста.\n\n"
        "Пример: `Показывай только B2B SaaS продукты с LLM API, которые можно повторить командой до 5 человек.`\n\n"
        "Чтобы сбросить — отправьте `-`",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(SettingsStates.waiting_user_prompt)
async def process_user_prompt(message: Message, user: User | None, state: FSMContext, session: AsyncSession):
    if not user:
        await state.clear()
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ Введите текст или `-` для сброса.", reply_markup=cancel_keyboard())
        return
    if value == "-":
        value = ""
    await update_user_settings(session, user.id, user_prompt=value)
    await state.clear()
    await message.answer(
        f"✅ Персональный prompt {'сброшен' if not value else 'обновлён'}.\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
