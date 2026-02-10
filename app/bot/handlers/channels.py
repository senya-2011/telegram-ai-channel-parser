from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import cancel_keyboard, channels_keyboard, main_menu_keyboard
from app.bot.states import AddChannelStates
from app.db.models import User
from app.db.repositories import (
    get_or_create_source,
    get_user_sources,
    subscribe_user_to_source,
    unsubscribe_user_from_source,
)

router = Router()


@router.callback_query(F.data == "menu:channels")
async def show_channels(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    channels = await get_user_sources(session, user.id, source_type="telegram")
    if channels:
        text = "📡 **Ваши Telegram-каналы:**\n\nНажмите ❌ чтобы отписаться:"
    else:
        text = "📡 **У вас пока нет каналов.**\n\nДобавьте первый канал:"

    await callback.message.edit_text(
        text,
        reply_markup=channels_keyboard(list(channels)),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "add:channel")
async def add_channel_start(callback: CallbackQuery, user: User | None, state: FSMContext):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await state.set_state(AddChannelStates.waiting_channel)
    await callback.message.edit_text(
        "Введите **username** канала (например, `@durov` или `durov`):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AddChannelStates.waiting_channel)
async def add_channel_process(message: Message, user: User | None, state: FSMContext, session: AsyncSession):
    if not user:
        await state.clear()
        return

    channel_input = message.text.strip()
    # Normalize: remove @ prefix, extract from t.me links
    if channel_input.startswith("https://t.me/"):
        channel_input = channel_input.replace("https://t.me/", "")
    elif channel_input.startswith("t.me/"):
        channel_input = channel_input.replace("t.me/", "")
    channel_input = channel_input.lstrip("@").strip("/")

    if not channel_input or len(channel_input) < 3:
        await message.answer(
            "❌ Некорректный username канала. Попробуйте снова:",
            reply_markup=cancel_keyboard(),
        )
        return

    identifier = f"@{channel_input}"
    source = await get_or_create_source(session, "telegram", identifier, title=identifier)
    subscribed = await subscribe_user_to_source(session, user.id, source.id)

    await state.clear()

    if subscribed:
        await message.answer(
            f"✅ Канал **{identifier}** добавлен!\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            f"ℹ️ Вы уже подписаны на **{identifier}**.\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )


@router.callback_query(F.data.startswith("unsub:channel:"))
async def unsubscribe_channel(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    source_id = int(callback.data.split(":")[-1])
    removed = await unsubscribe_user_from_source(session, user.id, source_id)

    if removed:
        await callback.answer("Канал удалён из подписок!")
    else:
        await callback.answer("Канал не найден.")

    # Refresh list
    channels = await get_user_sources(session, user.id, source_type="telegram")
    if channels:
        text = "📡 **Ваши Telegram-каналы:**\n\nНажмите ❌ чтобы отписаться:"
    else:
        text = "📡 **У вас пока нет каналов.**\n\nДобавьте первый канал:"

    await callback.message.edit_text(
        text,
        reply_markup=channels_keyboard(list(channels)),
        parse_mode="Markdown",
    )
