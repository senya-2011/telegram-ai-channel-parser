from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import auth_keyboard, main_menu_keyboard
from app.db.models import Alert, Post, Source, User, UserSource

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message, user: User | None, session: AsyncSession):
    """Command /status — show parsing stats."""
    if not user:
        await message.answer("Сначала авторизуйтесь:", reply_markup=auth_keyboard())
        return

    # Total sources for this user
    sources_count = await session.execute(
        select(func.count()).select_from(UserSource).where(UserSource.user_id == user.id)
    )
    total_sources = sources_count.scalar() or 0

    # Total posts from user's sources
    user_source_ids = await session.execute(
        select(UserSource.source_id).where(UserSource.user_id == user.id)
    )
    source_ids = [row[0] for row in user_source_ids.all()]

    total_posts = 0
    posts_24h = 0
    processed_posts = 0
    if source_ids:
        import datetime
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

        r1 = await session.execute(
            select(func.count()).select_from(Post).where(Post.source_id.in_(source_ids))
        )
        total_posts = r1.scalar() or 0

        r2 = await session.execute(
            select(func.count()).select_from(Post).where(
                Post.source_id.in_(source_ids), Post.parsed_at >= cutoff
            )
        )
        posts_24h = r2.scalar() or 0

        r3 = await session.execute(
            select(func.count()).select_from(Post).where(
                Post.source_id.in_(source_ids), Post.summary.isnot(None)
            )
        )
        processed_posts = r3.scalar() or 0

    # Alerts for this user
    alerts_count = await session.execute(
        select(func.count()).select_from(Alert).where(Alert.user_id == user.id)
    )
    total_alerts = alerts_count.scalar() or 0

    await message.answer(
        f"📊 **Статус системы**\n\n"
        f"📡 Ваших источников: **{total_sources}**\n"
        f"📝 Постов всего: **{total_posts}**\n"
        f"🕐 Постов за 24ч: **{posts_24h}**\n"
        f"🤖 Обработано (summary): **{processed_posts}**\n"
        f"🔔 Ваших алертов: **{total_alerts}**\n\n"
        f"_Парсинг каналов: каждые 10 мин_\n"
        f"_Парсинг ссылок: каждые 30 мин_",
        parse_mode="Markdown",
    )


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
        "/status — статистика парсинга\n"
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
