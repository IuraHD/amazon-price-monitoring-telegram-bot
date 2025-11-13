from aiogram.fsm.state import StatesGroup, State

class AddTracking(StatesGroup):
    choosing_folder = State()
    waiting_for_url = State()

class CreateFolder(StatesGroup):
    waiting_for_name = State()
    waiting_for_emoji = State()

class NavigationContext(StatesGroup):
    """Track current navigation context for number shortcuts."""
    main_menu = State()
    product_list = State()
    folder_list = State()
    product_detail = State()

