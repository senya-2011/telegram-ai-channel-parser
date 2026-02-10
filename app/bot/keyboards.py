from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Source


# ──────────────────────── Auth ────────────────────────

def auth_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Войти", callback_data="auth:login"),
            InlineKeyboardButton(text="Регистрация", callback_data="auth:register"),
        ]
    ])


# ──────────────────────── Main Menu ────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 Мои каналы", callback_data="menu:channels")],
        [InlineKeyboardButton(text="🔗 Мои ссылки", callback_data="menu:links")],
        [InlineKeyboardButton(text="📰 Дайджест за сегодня", callback_data="menu:digest")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings")],
    ])


# ──────────────────────── Channels ────────────────────────

def channels_keyboard(channels: list[Source]) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        title = ch.title or ch.identifier
        buttons.append([InlineKeyboardButton(text=f"❌ {title}", callback_data=f"unsub:channel:{ch.id}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="add:channel")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ──────────────────────── Links ────────────────────────

def links_keyboard(links: list[Source]) -> InlineKeyboardMarkup:
    buttons = []
    for link in links:
        title = link.title or link.identifier
        display = title[:40] + "..." if len(title) > 40 else title
        buttons.append([InlineKeyboardButton(text=f"❌ {display}", callback_data=f"unsub:link:{link.id}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить ссылку", callback_data="add:link")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ──────────────────────── Settings ────────────────────────

def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕐 Время дайджеста", callback_data="settings:digest_time")],
        [InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="settings:timezone")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


# ──────────────────────── Back / Cancel ────────────────────────

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])
