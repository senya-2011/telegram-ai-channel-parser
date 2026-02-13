from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import auth_keyboard, main_menu_keyboard
from app.db.models import Alert, NewsCluster, Post, User, UserSource

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
    total_clusters = 0
    clusters_24h = 0
    clusters_product_24h = 0
    clusters_trend_24h = 0
    clusters_research_24h = 0
    clusters_misc_24h = 0
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

        r4 = await session.execute(
            select(func.count(func.distinct(Post.cluster_id))).select_from(Post).where(
                Post.source_id.in_(source_ids), Post.cluster_id.isnot(None)
            )
        )
        total_clusters = r4.scalar() or 0

        r5 = await session.execute(
            select(func.count(func.distinct(Post.cluster_id))).select_from(Post).where(
                Post.source_id.in_(source_ids),
                Post.cluster_id.isnot(None),
                Post.parsed_at >= cutoff,
            )
        )
        clusters_24h = r5.scalar() or 0

        cluster_ids_result = await session.execute(
            select(func.distinct(Post.cluster_id)).where(
                Post.source_id.in_(source_ids),
                Post.cluster_id.isnot(None),
                Post.parsed_at >= cutoff,
            )
        )
        cluster_ids_24h = [row[0] for row in cluster_ids_result.all() if row[0]]
        if cluster_ids_24h:
            kind_rows = await session.execute(
                select(NewsCluster.news_kind, func.count())
                .where(NewsCluster.id.in_(cluster_ids_24h))
                .group_by(NewsCluster.news_kind)
            )
            kind_map = {kind: cnt for kind, cnt in kind_rows.all()}
            clusters_product_24h = int(kind_map.get("product", 0))
            clusters_trend_24h = int(kind_map.get("trend", 0))
            clusters_research_24h = int(kind_map.get("research", 0))
            clusters_misc_24h = int(kind_map.get("misc", 0))

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
        f"🤖 Обработано постов: **{processed_posts}**\n"
        f"🧩 Кластеров новостей всего: **{total_clusters}**\n"
        f"🕐 Кластеров за 24ч: **{clusters_24h}**\n"
        f"📦 Product/Trend/Research/Misc (24ч): "
        f"**{clusters_product_24h}/{clusters_trend_24h}/{clusters_research_24h}/{clusters_misc_24h}**\n"
        f"🔔 Ваших алертов: **{total_alerts}**\n\n"
        f"_Парсинг каналов: каждые 10 мин_\n"
        f"_Парсинг ссылок/API: каждые 30 мин_",
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
        "/quality — качество ленты (product/trend/research/misc)\n"
        "/help — справка по командам",
        parse_mode="Markdown",
    )


@router.message(Command("quality"))
async def cmd_quality(message: Message, user: User | None, session: AsyncSession):
    """Command /quality — show feed quality metrics for the last 24 hours."""
    if not user:
        await message.answer("Сначала авторизуйтесь:", reply_markup=auth_keyboard())
        return

    import datetime
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

    user_source_ids = await session.execute(
        select(UserSource.source_id).where(UserSource.user_id == user.id)
    )
    source_ids = [row[0] for row in user_source_ids.all()]
    if not source_ids:
        await message.answer("Нет подписанных источников. Добавьте источники и попробуйте снова.")
        return

    cluster_ids_result = await session.execute(
        select(func.distinct(Post.cluster_id)).where(
            Post.source_id.in_(source_ids),
            Post.cluster_id.isnot(None),
            Post.parsed_at >= cutoff,
        )
    )
    cluster_ids_24h = [row[0] for row in cluster_ids_result.all() if row[0]]
    if not cluster_ids_24h:
        await message.answer("За последние 24 часа кластеров пока нет.")
        return

    kinds = await session.execute(
        select(NewsCluster.news_kind, func.count())
        .where(NewsCluster.id.in_(cluster_ids_24h))
        .group_by(NewsCluster.news_kind)
    )
    kind_map = {kind: int(cnt) for kind, cnt in kinds.all()}
    product = kind_map.get("product", 0)
    trend = kind_map.get("trend", 0)
    research = kind_map.get("research", 0)
    misc = kind_map.get("misc", 0)
    total = product + trend + research + misc
    product_share = (product / total * 100) if total else 0.0

    alert_types = await session.execute(
        select(Alert.alert_type, func.count())
        .where(Alert.user_id == user.id, Alert.created_at >= cutoff)
        .group_by(Alert.alert_type)
    )
    alert_map = {atype: int(cnt) for atype, cnt in alert_types.all()}

    await message.answer(
        f"🎯 **Качество ленты (24ч)**\n\n"
        f"🧩 Кластеры: **{total}**\n"
        f"📦 Product: **{product}** ({product_share:.1f}%)\n"
        f"📈 Trend: **{trend}**\n"
        f"🧪 Research: **{research}**\n"
        f"🗂 Misc: **{misc}**\n\n"
        f"🔔 Алерты (24ч):\n"
        f"- important: **{alert_map.get('important', 0)}**\n"
        f"- similar: **{alert_map.get('similar', 0)}**\n"
        f"- trend: **{alert_map.get('trend', 0)}**\n"
        f"- reactions: **{alert_map.get('reactions', 0)}**",
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


@router.callback_query(F.data == "menu:new")
async def show_main_menu_new_message(callback: CallbackQuery, user: User | None, state: FSMContext):
    await state.clear()
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.message.answer(
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
