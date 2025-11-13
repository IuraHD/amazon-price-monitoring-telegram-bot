from aiogram.fsm.state import StatesGroup, State

class AddTracking(StatesGroup):
    choosing_folder = State()
    waiting_for_url = State()

class CreateFolder(StatesGroup):
    waiting_for_name = State()
    waiting_for_emoji = State()
