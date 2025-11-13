from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData

# Number emoji helper
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

def get_number_emoji(num: int) -> str:
    """Get number emoji for given number (1-9)."""
    if 1 <= num <= 9:
        return NUMBER_EMOJIS[num - 1]
    return str(num)

class ProductActions(CallbackData, prefix="prod"):
    action: str
    id: int
    page: int = 1

class MenuActions(CallbackData, prefix="menu"):
    action: str
    page: int = 1

class FolderActions(CallbackData, prefix="folder"):
    action: str
    id: int = 0  # 0 for "no folder" or "create new"


def main_menu() -> InlineKeyboardMarkup:
    """Main menu with numbered shortcuts (1-5)."""
    b = InlineKeyboardBuilder()
    b.button(text="1️⃣ Add Product", callback_data=MenuActions(action="add").pack())
    b.button(text="2️⃣ My Folders", callback_data=MenuActions(action="folders").pack())
    b.button(text="3️⃣ All Products", callback_data=MenuActions(action="list").pack())
    b.button(text="4️⃣ Refresh All", callback_data=MenuActions(action="refresh_all").pack())
    b.button(text="5️⃣ Settings", callback_data=MenuActions(action="settings").pack())
    b.adjust(1)
    return b.as_markup()


def products_list(rows, show_folder: bool = False, page: int = 1) -> InlineKeyboardMarkup:
    """Product list with numbered items (1-9 per page)."""
    b = InlineKeyboardBuilder()
    
    # Paginate to show max 9 items per page
    page_size = 9
    start = (page - 1) * page_size
    end = min(start + page_size, len(rows))
    paginated_rows = rows[start:end]
    
    for idx, r in enumerate(paginated_rows, 1):
        num_emoji = get_number_emoji(idx)
        price_str = f"£{r['last_price']:.2f}" if r['last_price'] is not None else 'n/a'
        
        # Truncate product name for cleaner display
        name = r.get('title') or r['asin']
        name = name[:20] + "..." if len(name) > 20 else name
        
        if show_folder and "folder_name" in r.keys() and r['folder_name']:
            emoji = r['folder_emoji'] if 'folder_emoji' in r.keys() else '📁'
            label = f"{num_emoji} {name} - {price_str} {emoji}"
        else:
            label = f"{num_emoji} {name} - {price_str}"
        
        b.button(text=label, callback_data=ProductActions(action="show", id=r['id'], page=page).pack())
    
    # Pagination controls
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️ Previous",
            callback_data=MenuActions(action="list", page=page-1).pack()
        ))
    if end < len(rows):
        nav_buttons.append(InlineKeyboardButton(
            text="Next ▶️",
            callback_data=MenuActions(action="list", page=page+1).pack()
        ))
    
    if nav_buttons:
        b.row(*nav_buttons)
    
    # Page indicator and back button
    total_pages = (len(rows) + page_size - 1) // page_size
    if total_pages > 1:
        b.button(text=f"📄 Page {page}/{total_pages}", callback_data=MenuActions(action="noop").pack())
    
    b.button(text="⬅️ Back", callback_data=MenuActions(action="back").pack())
    b.adjust(1)
    return b.as_markup()


def product_detail(row, page: int = 1) -> InlineKeyboardMarkup:
    """Product detail actions with numbered shortcuts (1-5)."""
    b = InlineKeyboardBuilder()
    alerts = "🔔 Alerts ON" if row["alerts_enabled"] else "🔕 Alerts OFF"
    b.button(text=f"1️⃣ {alerts}", callback_data=ProductActions(action="toggle", id=row['id'], page=page).pack())
    b.button(text="2️⃣ 🔄 Refresh Price", callback_data=ProductActions(action="refresh", id=row['id'], page=page).pack())
    b.button(text="3️⃣ 📂 Move to Folder", callback_data=ProductActions(action="move", id=row['id'], page=page).pack())
    b.button(text="4️⃣ 🗑️ Remove", callback_data=ProductActions(action="remove", id=row['id'], page=page).pack())
    b.button(text="⬅️ Back to List", callback_data=ProductActions(action="back_to_list", id=row['id'], page=page).pack())
    b.adjust(1)
    return b.as_markup()


def add_tracking_menu() -> InlineKeyboardMarkup:
    """Keyboard shown while waiting for URL input, allowing user to go back."""
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Back", callback_data=MenuActions(action="back").pack())
    b.adjust(1)
    return b.as_markup()


def choose_folder_menu(folders, include_none: bool = True) -> InlineKeyboardMarkup:
    """Keyboard for choosing a folder when adding a product."""
    b = InlineKeyboardBuilder()
    
    for folder in folders:
        emoji = folder.get("emoji", "📁")
        name = folder["name"]
        count = folder.get("count", 0)
        label = f"{emoji} {name} ({count})"
        b.button(text=label, callback_data=FolderActions(action="select", id=folder["id"]).pack())
    
    if include_none:
        b.button(text="📦 No Folder", callback_data=FolderActions(action="select", id=0).pack())
    
    b.button(text="➕ Create New Folder", callback_data=FolderActions(action="create", id=0).pack())
    b.button(text="⬅️ Back", callback_data=MenuActions(action="back").pack())
    b.adjust(1)
    return b.as_markup()


def folders_list_menu(folders, page: int = 1) -> InlineKeyboardMarkup:
    """Keyboard showing user folders with numbered shortcuts (1-9 per page)."""
    b = InlineKeyboardBuilder()
    
    # Paginate to show max 9 folders per page
    page_size = 9
    start = (page - 1) * page_size
    end = min(start + page_size, len(folders))
    paginated_folders = folders[start:end]
    
    for idx, folder in enumerate(paginated_folders, 1):
        num_emoji = get_number_emoji(idx)
        emoji = folder.get("emoji", "📁")
        name = folder["name"]
        count = folder.get("count", 0)
        label = f"{num_emoji} {emoji} {name} ({count})"
        b.button(text=label, callback_data=FolderActions(action="view", id=folder["id"]).pack())
    
    # Pagination controls
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️ Previous",
            callback_data=MenuActions(action="folders", page=page-1).pack()
        ))
    if end < len(folders):
        nav_buttons.append(InlineKeyboardButton(
            text="Next ▶️",
            callback_data=MenuActions(action="folders", page=page+1).pack()
        ))
    
    if nav_buttons:
        b.row(*nav_buttons)
    
    # Page indicator
    total_pages = (len(folders) + page_size - 1) // page_size if folders else 1
    if total_pages > 1:
        b.button(text=f"📄 Page {page}/{total_pages}", callback_data=MenuActions(action="noop").pack())
    
    b.button(text="📦 Uncategorized", callback_data=FolderActions(action="view", id=0).pack())
    b.button(text="➕ New Folder", callback_data=FolderActions(action="create", id=0).pack())
    b.button(text="⬅️ Back", callback_data=MenuActions(action="back").pack())
    b.adjust(1)
    return b.as_markup()


def folder_detail_menu(folder_id: int) -> InlineKeyboardMarkup:
    """Keyboard for managing a specific folder."""
    b = InlineKeyboardBuilder()
    if folder_id != 0:  # Can't edit "No Folder"
        b.button(text="✏️ Rename", callback_data=FolderActions(action="rename", id=folder_id).pack())
        b.button(text="🎨 Change Emoji", callback_data=FolderActions(action="emoji", id=folder_id).pack())
        b.button(text="🗑️ Delete Folder", callback_data=FolderActions(action="delete", id=folder_id).pack())
    b.button(text="⬅️ Back to Folders", callback_data=MenuActions(action="folders").pack())
    b.adjust(1)
    return b.as_markup()
