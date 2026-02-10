from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import cancel_keyboard, links_keyboard, main_menu_keyboard
from app.bot.states import AddLinkStates
from app.db.models import User
from app.db.repositories import (
    get_or_create_source,
    get_user_sources,
    subscribe_user_to_source,
    unsubscribe_user_from_source,
)

router = Router()


@router.callback_query(F.data == "menu:links")
async def show_links(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    links = await get_user_sources(session, user.id, source_type="web")
    if links:
        text = "🔗 **Ваши веб-ссылки:**\n\nНажмите ❌ чтобы отписаться:"
    else:
        text = "🔗 **У вас пока нет ссылок.**\n\nДобавьте первую ссылку:"

    await callback.message.edit_text(
        text,
        reply_markup=links_keyboard(list(links)),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "add:link")
async def add_link_start(callback: CallbackQuery, user: User | None, state: FSMContext):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await state.set_state(AddLinkStates.waiting_link)
    await callback.message.edit_text(
        "Введите **URL** сайта (например, `https://habr.com/ru/flows/develop/`):",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AddLinkStates.waiting_link)
async def add_link_process(message: Message, user: User | None, state: FSMContext, session: AsyncSession):
    if not user:
        await state.clear()
        return

    url = message.text.strip()

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # Basic URL validation
    if "." not in url or len(url) < 10:
        await message.answer(
            "❌ Некорректный URL. Введите полный адрес сайта:",
            reply_markup=cancel_keyboard(),
        )
        return

    source = await get_or_create_source(session, "web", url, title=url)
    subscribed = await subscribe_user_to_source(session, user.id, source.id)

    await state.clear()

    if subscribed:
        await message.answer(
            f"✅ Ссылка добавлена!\n`{url}`\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            f"ℹ️ Вы уже отслеживаете эту ссылку.\n\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )


@router.callback_query(F.data.startswith("unsub:link:"))
async def unsubscribe_link(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    source_id = int(callback.data.split(":")[-1])
    removed = await unsubscribe_user_from_source(session, user.id, source_id)

    if removed:
        await callback.answer("Ссылка удалена из подписок!")
    else:
        await callback.answer("Ссылка не найдена.")

    # Refresh list
    links = await get_user_sources(session, user.id, source_type="web")
    if links:
        text = "🔗 **Ваши веб-ссылки:**\n\nНажмите ❌ чтобы отписаться:"
    else:
        text = "🔗 **У вас пока нет ссылок.**\n\nДобавьте первую ссылку:"

    await callback.message.edit_text(
        text,
        reply_markup=links_keyboard(list(links)),
        parse_mode="Markdown",
    )
