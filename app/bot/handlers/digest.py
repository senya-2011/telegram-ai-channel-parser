import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import back_to_menu_new_keyboard, digest_keyboard, discovered_sources_keyboard
from app.db.models import User
from app.db.repositories import (
    get_cluster_by_id,
    get_or_create_source,
    get_posts_for_digest,
    get_source_by_id,
    get_user_sources,
    subscribe_user_to_source,
    upsert_user_feedback,
)
from app.services.digest import generate_digest_for_user

logger = logging.getLogger(__name__)

router = Router()


async def _safe_send(message, text: str, reply_markup=None):
    """Send message with HTML, fallback to plain text if parsing fails."""
    try:
        return await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        try:
            return await message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            return await message.answer(text, reply_markup=reply_markup)


async def _safe_edit(message, text: str, reply_markup=None):
    """Edit message with HTML, fallback to plain text if parsing fails."""
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        try:
            return await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            return await message.answer(text, reply_markup=reply_markup)


def _split_text_smart(text: str, max_len: int = 4000) -> list[str]:
    """Split text into chunks at line boundaries, never breaking Markdown."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        # If adding this line would exceed limit, start a new chunk
        if len(current_chunk) + len(line) + 1 > max_len:
            if current_chunk:
                chunks.append(current_chunk.rstrip())
            current_chunk = line + "\n"
            # If a single line is too long, force-split it
            while len(current_chunk) > max_len:
                chunks.append(current_chunk[:max_len])
                current_chunk = current_chunk[max_len:]
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.rstrip())

    return chunks if chunks else [text[:max_len]]


async def _render_digest(
    callback: CallbackQuery,
    user: User | None,
    session: AsyncSession,
    mode: str = "main",
):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.answer()
    mode_title = {
        "main": "основной",
        "tech_update": "технологических обновлений",
        "industry_report": "отчётов и аналитики",
    }.get(mode, "основной")
    await callback.message.edit_text(f"⏳ Формирую дайджест ({mode_title})...")

    try:
        digest_text = await generate_digest_for_user(session, user.id, mode=mode)
        if digest_text:
            chunks = _split_text_smart(digest_text, max_len=4000)
            if len(chunks) == 1:
                await _safe_edit(callback.message, chunks[0], reply_markup=digest_keyboard())
            else:
                # Delete the "loading" message first
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        await _safe_send(callback.message, chunk, reply_markup=digest_keyboard())
                    else:
                        await _safe_send(callback.message, chunk)
        else:
            await callback.message.edit_text(
                "📭 За сегодня новостей пока нет.\n\n"
                "Убедитесь, что вы подписаны на каналы и ссылки.",
                reply_markup=digest_keyboard(),
            )
    except Exception as e:
        logger.error(f"Error generating digest: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при формировании дайджеста. Попробуйте позже.",
            reply_markup=back_to_menu_new_keyboard(),
        )


@router.callback_query(F.data == "menu:digest")
async def show_digest(callback: CallbackQuery, user: User | None, session: AsyncSession):
    await _render_digest(callback, user, session, mode="main")


@router.callback_query(F.data == "menu:digest:tech_update")
async def show_tech_digest(callback: CallbackQuery, user: User | None, session: AsyncSession):
    await _render_digest(callback, user, session, mode="tech_update")


@router.callback_query(F.data == "menu:digest:industry_report")
async def show_reports_digest(callback: CallbackQuery, user: User | None, session: AsyncSession):
    await _render_digest(callback, user, session, mode="industry_report")


@router.callback_query(F.data.startswith("feedback:"))
async def save_feedback(callback: CallbackQuery, user: User | None, session: AsyncSession):
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    direction = parts[1]
    try:
        cluster_id = int(parts[2])
    except Exception:
        await callback.answer("Некорректный cluster_id", show_alert=True)
        return

    cluster = await get_cluster_by_id(session, cluster_id)
    if not cluster:
        await callback.answer("Новость не найдена", show_alert=True)
        return

    vote = 1 if direction == "up" else -1
    await upsert_user_feedback(session, user.id, cluster_id, vote=vote)
    await callback.answer("✅ Учтено, подстрою ленту под вас")


@router.callback_query(F.data == "discover:sources")
async def discover_sources(callback: CallbackQuery, user: User | None, session: AsyncSession, state: FSMContext):
    """Search for new sources based on current digest topics. Sends as NEW message, digest stays."""
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.answer("🔍 Ищу источники...")

    # Send a NEW loading message (digest stays untouched above)
    loading_msg = await callback.message.answer("🔍 Ищу новые источники по вашим темам...")

    try:
        from app.services.web_search import extract_topics_from_summaries, search_related_sources

        # Get recent post summaries to extract topics
        sources = await get_user_sources(session, user.id)
        source_ids = [s.id for s in sources]
        posts = await get_posts_for_digest(session, source_ids, hours=24, limit=15)

        summaries = [p.summary for p in posts if p.summary]

        if not summaries:
            await loading_msg.edit_text(
                "📭 Недостаточно данных для поиска — нужны посты за сегодня.\n"
                "Попробуйте позже, когда накопятся новости.",
                reply_markup=back_to_menu_new_keyboard(),
            )
            return

        # Extract topics
        topics = await extract_topics_from_summaries(summaries)

        # Search for related sources
        discovered = await search_related_sources(topics, max_results=8)

        if not discovered:
            await loading_msg.edit_text(
                "🤷 Не удалось найти новые источники. Попробуйте позже.",
                reply_markup=back_to_menu_new_keyboard(),
            )
            return

        # Save discovered sources in FSM state for subscription
        await state.update_data(discovered_sources=discovered)

        # Format results in HTML
        tg_count = sum(1 for r in discovered if r.get("type") == "telegram")
        non_tg_count = len(discovered) - tg_count
        text = f'🔍 <b>Найдено {len(discovered)} источников</b> (📡 {tg_count} каналов, 🔗 {non_tg_count} API/Web)\n'
        text += f'<i>Темы: {", ".join(topics)}</i>\n\n'
        for i, src in enumerate(discovered):
            emoji = "📡" if src.get("type") == "telegram" else "🔗"
            snippet = src["snippet"][:100] + "..." if len(src["snippet"]) > 100 else src["snippet"]
            text += f'<b>{i + 1}. {emoji} {src["title"]}</b>\n{snippet}\n\n'

        text += "Нажмите ➕ чтобы подписаться на источник:"

        if len(text) > 4000:
            text = text[:3950] + "\n\n..."

        try:
            await loading_msg.edit_text(
                text,
                reply_markup=discovered_sources_keyboard(discovered),
                parse_mode="HTML",
            )
        except Exception:
            await loading_msg.edit_text(
                text,
                reply_markup=discovered_sources_keyboard(discovered),
            )

    except Exception as e:
        logger.error(f"Error discovering sources: {e}")
        await loading_msg.edit_text(
            "❌ Ошибка при поиске источников. Попробуйте позже.",
            reply_markup=back_to_menu_new_keyboard(),
        )


@router.callback_query(F.data.startswith("addsrc:"))
async def add_discovered_source(
    callback: CallbackQuery, user: User | None, session: AsyncSession, state: FSMContext
):
    """Subscribe to a discovered source."""
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    data = await state.get_data()
    discovered = data.get("discovered_sources", [])

    idx = int(callback.data.split(":")[1])
    if idx >= len(discovered):
        await callback.answer("Источник не найден.", show_alert=True)
        return

    src = discovered[idx]
    title = src["title"]
    src_type = src.get("type", "web")

    # Determine source type and identifier
    if src_type == "telegram":
        identifier = f"@{title.lstrip('@')}"
        source = await get_or_create_source(session, "telegram", identifier, title=src.get("snippet", identifier))
    elif src_type in {"reddit", "github", "producthunt"}:
        identifier = src.get("identifier") or src.get("url") or title
        source = await get_or_create_source(session, src_type, identifier, title=title)
    else:
        source = await get_or_create_source(session, "web", src["url"], title=title)

    subscribed = await subscribe_user_to_source(session, user.id, source.id)

    emoji = "📡" if src_type == "telegram" else "🔗"
    if subscribed:
        await callback.answer(f"✅ {emoji} Подписка на: {title[:40]}", show_alert=True)
    else:
        await callback.answer("Вы уже подписаны на этот источник", show_alert=True)

    # Update keyboard — mark subscribed
    discovered[idx]["subscribed"] = True
    await state.update_data(discovered_sources=discovered)


@router.callback_query(F.data.startswith("alertsrc:"))
async def search_alert_sources(callback: CallbackQuery, user: User | None, session: AsyncSession, state: FSMContext):
    """Search for more sources about an alert topic."""
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.answer("🔍 Ищу источники...")

    loading_msg = await callback.message.answer("🔍 Ищу источники и информацию по теме алерта...")

    try:
        from app.services.web_search import search_related_sources

        # Extract topic from the alert message text
        alert_text = callback.message.text or ""
        # Get the news summary from the alert
        topic_line = ""
        for line in alert_text.split("\n"):
            if "Новость:" in line or "новость" in line.lower():
                topic_line = line.replace("Новость:", "").replace("📰", "").strip()
                break
        if not topic_line:
            topic_line = alert_text[:150]

        # Search for sources about this topic
        topics = [topic_line[:80]]
        discovered = await search_related_sources(topics, max_results=8)

        if not discovered:
            await loading_msg.edit_text(
                "🤷 Не удалось найти дополнительные источники по этой теме.",
                reply_markup=back_to_menu_new_keyboard(),
            )
            return

        await state.update_data(discovered_sources=discovered)

        tg_count = sum(1 for r in discovered if r.get("type") == "telegram")
        non_tg_count = len(discovered) - tg_count
        text = f'🔍 <b>Найдено {len(discovered)} источников по теме алерта</b> '
        text += f'(📡 {tg_count} каналов, 🔗 {non_tg_count} API/Web)\n\n'
        for i, src in enumerate(discovered):
            emoji = "📡" if src.get("type") == "telegram" else "🔗"
            snippet = src["snippet"][:100] + "..." if len(src["snippet"]) > 100 else src["snippet"]
            text += f'<b>{i + 1}. {emoji} {src["title"]}</b>\n{snippet}\n\n'

        text += "Нажмите ➕ чтобы подписаться:"

        if len(text) > 4000:
            text = text[:3950] + "\n\n..."

        try:
            await loading_msg.edit_text(
                text,
                reply_markup=discovered_sources_keyboard(discovered),
                parse_mode="HTML",
            )
        except Exception:
            await loading_msg.edit_text(
                text,
                reply_markup=discovered_sources_keyboard(discovered),
            )

    except Exception as e:
        logger.error(f"Error searching alert sources: {e}")
        await loading_msg.edit_text(
            "❌ Ошибка при поиске. Попробуйте позже.",
            reply_markup=back_to_menu_new_keyboard(),
        )
