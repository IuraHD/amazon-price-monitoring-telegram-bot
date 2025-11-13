from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData

class ProductActions(CallbackData, prefix="prod"):
    action: str
    id: int

class MenuActions(CallbackData, prefix="menu"):
    action: str

class FolderActions(CallbackData, prefix="folder"):
    action: str
    id: int = 0  # 0 for "no folder" or "create new"


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Add Product", callback_data=MenuActions(action="add").pack())
    b.button(text="📂 My Folders", callback_data=MenuActions(action="folders").pack())
    b.button(text="📦 All Products", callback_data=MenuActions(action="list").pack())
    b.button(text="🔄 Refresh All Prices", callback_data=MenuActions(action="refresh_all").pack())
    b.adjust(1)
    return b.as_markup()


def products_list(rows, show_folder: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in rows:
        price_str = f"£{r['last_price']:.2f}" if r['last_price'] is not None else 'n/a'
        if show_folder and "folder_name" in r.keys() and r['folder_name']:
            emoji = r['folder_emoji'] if 'folder_emoji' in r.keys() else '📁'
            label = f"{r['asin']} ({price_str}) - {emoji} {r['folder_name']}"
        else:
            label = f"{r['asin']} ({price_str})"
        b.button(text=label, callback_data=ProductActions(action="show", id=r['id']).pack())
    b.button(text="⬅️ Back", callback_data=MenuActions(action="back").pack())
    b.adjust(1)
    return b.as_markup()


def product_detail(row) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    alerts = "🔔 Alerts ON" if row["alerts_enabled"] else "🔕 Alerts OFF"
    b.button(text=alerts, callback_data=ProductActions(action="toggle", id=row['id']).pack())
    b.button(text="🔄 Refresh Price", callback_data=ProductActions(action="refresh", id=row['id']).pack())
    b.button(text="📂 Move to Folder", callback_data=ProductActions(action="move", id=row['id']).pack())
    b.button(text="🗑️ Remove", callback_data=ProductActions(action="remove", id=row['id']).pack())
    b.button(text="⬅️ Back to List", callback_data=ProductActions(action="back_to_list", id=row['id']).pack())
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


def folders_list_menu(folders) -> InlineKeyboardMarkup:
    """Keyboard showing all user folders for management."""
    b = InlineKeyboardBuilder()
    
    for folder in folders:
        emoji = folder.get("emoji", "📁")
        name = folder["name"]
        count = folder.get("count", 0)
        label = f"{emoji} {name} ({count})"
        b.button(text=label, callback_data=FolderActions(action="view", id=folder["id"]).pack())
    
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
