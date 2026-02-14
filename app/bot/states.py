from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()


class RegisterStates(StatesGroup):
    waiting_username = State()
    waiting_password = State()
    waiting_password_confirm = State()


class AddChannelStates(StatesGroup):
    waiting_channel = State()


class AddLinkStates(StatesGroup):
    waiting_link = State()


class SettingsStates(StatesGroup):
    waiting_digest_time = State()
    waiting_timezone = State()
    waiting_user_prompt = State()
