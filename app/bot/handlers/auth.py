from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import auth_keyboard, main_menu_keyboard
from app.bot.states import AuthStates, RegisterStates
from app.db.models import User
from app.db.repositories import (
    authenticate_user,
    create_user,
    get_user_by_username,
    link_telegram_account,
    subscribe_user_to_defaults,
)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None, state: FSMContext):
    await state.clear()
    if user:
        await message.answer(
            f"👋 Добро пожаловать, **{user.username}**!\nВыберите действие:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            "👋 Добро пожаловать в **AI News Parser**!\n\n"
            "Этот бот отслеживает Telegram-каналы и веб-ссылки, "
            "анализирует новости и присылает важные алерты.\n\n"
            "Для начала войдите или зарегистрируйтесь:",
            reply_markup=auth_keyboard(),
            parse_mode="Markdown",
        )


# ──────────────────────── Login ────────────────────────

@router.callback_query(F.data == "auth:login")
async def login_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AuthStates.waiting_login)
    await callback.message.edit_text("Введите ваш **логин**:", parse_mode="Markdown")
    await callback.answer()


@router.message(AuthStates.waiting_login)
async def login_username(message: Message, state: FSMContext):
    await state.update_data(username=message.text.strip())
    await state.set_state(AuthStates.waiting_password)
    await message.answer("Введите ваш **пароль**:", parse_mode="Markdown")


@router.message(AuthStates.waiting_password)
async def login_password(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    username = data["username"]
    password = message.text.strip()

    # Delete password message for security
    try:
        await message.delete()
    except Exception:
        pass

    user = await authenticate_user(session, username, password)
    if not user:
        await state.clear()
        await message.answer(
            "❌ Неверный логин или пароль.\nПопробуйте снова:",
            reply_markup=auth_keyboard(),
        )
        return

    # Link current Telegram account to user
    try:
        await link_telegram_account(session, user.id, message.from_user.id)
    except Exception:
        pass  # Already linked

    await state.clear()
    await message.answer(
        f"✅ Вы вошли как **{user.username}**!\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


# ──────────────────────── Register ────────────────────────

@router.callback_query(F.data == "auth:register")
async def register_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RegisterStates.waiting_username)
    await callback.message.edit_text(
        "📝 **Регистрация**\n\nПридумайте **логин** (латинские буквы, цифры, 3-30 символов):",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(RegisterStates.waiting_username)
async def register_username(message: Message, state: FSMContext, session: AsyncSession):
    username = message.text.strip()

    if len(username) < 3 or len(username) > 30 or not username.isalnum():
        await message.answer(
            "❌ Логин должен содержать 3-30 символов (только латинские буквы и цифры).\n"
            "Попробуйте другой:"
        )
        return

    existing = await get_user_by_username(session, username)
    if existing:
        await message.answer("❌ Такой логин уже занят. Выберите другой:")
        return

    await state.update_data(username=username)
    await state.set_state(RegisterStates.waiting_password)
    await message.answer("Придумайте **пароль** (минимум 6 символов):", parse_mode="Markdown")


@router.message(RegisterStates.waiting_password)
async def register_password(message: Message, state: FSMContext):
    password = message.text.strip()

    # Delete password message for security
    try:
        await message.delete()
    except Exception:
        pass

    if len(password) < 6:
        await message.answer("❌ Пароль должен быть минимум 6 символов. Попробуйте снова:")
        return

    await state.update_data(password=password)
    await state.set_state(RegisterStates.waiting_password_confirm)
    await message.answer("Подтвердите **пароль** (введите ещё раз):", parse_mode="Markdown")


@router.message(RegisterStates.waiting_password_confirm)
async def register_password_confirm(message: Message, state: FSMContext, session: AsyncSession):
    confirm = message.text.strip()

    # Delete password message for security
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    if confirm != data["password"]:
        await state.set_state(RegisterStates.waiting_password)
        await message.answer("❌ Пароли не совпадают. Введите пароль заново:")
        return

    # Create user
    user = await create_user(session, data["username"], data["password"])

    # Link Telegram account
    await link_telegram_account(session, user.id, message.from_user.id)

    # Subscribe to default sources
    await subscribe_user_to_defaults(session, user.id)

    await state.clear()
    await message.answer(
        f"✅ Регистрация успешна! Добро пожаловать, **{user.username}**!\n\n"
        "Вы автоматически подписаны на базовый набор каналов.\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
