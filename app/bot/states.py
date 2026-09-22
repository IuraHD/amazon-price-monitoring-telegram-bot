from aiogram.fsm.state import State, StatesGroup


class Input(StatesGroup):
    url = State()
    folder_name = State()
    folder_emoji = State()
    rename = State()
    emoji = State()
    target = State()
    percentage = State()
    search = State()
