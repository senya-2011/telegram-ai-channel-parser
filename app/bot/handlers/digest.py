import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import back_to_menu_keyboard, digest_keyboard, discovered_sources_keyboard
from app.db.models import User
from app.db.repositories import (
    get_or_create_source,
    get_posts_for_digest,
    get_source_by_id,
    get_user_sources,
    subscribe_user_to_source,
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
            reply_markup=back_to_menu_keyboard(),
        )


@router.callback_query(F.data == "discover:sources")
async def discover_sources(callback: CallbackQuery, user: User | None, session: AsyncSession, state: FSMContext):
    """Search for new sources based on current digest topics. Sends as NEW message, digest stays."""
    if not user:
        await callback.answer("Сначала авторизуйтесь.", show_alert=True)
        return

    await callback.answer("🔍 Ищу источники...")

    # Remove the button from the digest message so it's not clicked again, but keep the text
    try:
        await callback.message.edit_reply_markup(reply_markup=back_to_menu_keyboard())
    except Exception:
        pass

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
                reply_markup=back_to_menu_keyboard(),
            )
            return

        # Extract topics
        topics = await extract_topics_from_summaries(summaries)

        # Search for related sources
        discovered = await search_related_sources(topics, max_results=8)

        if not discovered:
            await loading_msg.edit_text(
                "🤷 Не удалось найти новые источники. Попробуйте позже.",
                reply_markup=back_to_menu_keyboard(),
            )
            return

        # Save discovered sources in FSM state for subscription
        await state.update_data(discovered_sources=discovered)

        # Format results in HTML
        tg_count = sum(1 for r in discovered if r.get("type") == "telegram")
        web_count = sum(1 for r in discovered if r.get("type") == "web")
        text = f'🔍 <b>Найдено {len(discovered)} источников</b> (📡 {tg_count} каналов, 🔗 {web_count} сайтов)\n'
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
            reply_markup=back_to_menu_keyboard(),
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
