import asyncio
import signal
import structlog
import aiohttp
import html
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.core.config import load_settings
from app.core.db import init_db, execute
from app.core.products import (
    add_product,
    update_price,
    list_products,
    list_products_without_folder,
    list_all_products,
    get_product,
    list_price_history,
    toggle_alerts,
    remove_product,
    update_graph_message,
    update_title,
)
from app.core.folders import (
    create_folder,
    get_folder,
    list_folders,
    update_folder_emoji,
    rename_folder,
    delete_folder,
    count_products_in_folder,
)
from app.utils.fetch import fetch_price, extract_asin, resolve_asin, fetch_title
from urllib.parse import urlparse
from app.utils.graph import build_price_graph
from app.core.analytics import track_navigation_event, track_error_event
from .states import AddTracking, CreateFolder, NavigationContext
from .keyboards import (
    main_menu, products_list, product_detail, add_tracking_menu,
    choose_folder_menu, folders_list_menu, folder_detail_menu,
    MenuActions, ProductActions, FolderActions
)

settings = load_settings()
log = structlog.get_logger()
bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
router = Dispatcher()
PRICE_REFRESH_MINUTES = settings.price_refresh_minutes
_http_session: aiohttp.ClientSession | None = None
_rate_limit: dict[int, float] = {}  # product_id -> last fetch timestamp
ALERT_MIN_DELTA = 0.01  # minimal price change to alert
MIN_FETCH_INTERVAL = 60  # seconds per product to avoid spam

# ================= Helper Functions =================

async def safe_answer(callback: CallbackQuery, text: str = None, show_alert: bool = False):
    """Safely answer callback query, ignoring timeout errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            pass  # Ignore old callback queries
        else:
            raise


async def safe_edit(message, text, reply_markup=None):
    """Edit message text, catching 'message is not modified' errors."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ================= Number Shortcut Handlers =================

@router.message(F.text.regexp(r'^[1-9]$'))
async def handle_number_shortcut(message: Message, state: FSMContext):
    """Handle number shortcuts (1-9) based on current navigation context."""
    number = int(message.text)
    data = await state.get_data()
    context = data.get("nav_context", "main_menu")
    chat_id = message.chat.id
    
    # Route based on current navigation context
    if context == "main_menu":
        await handle_main_menu_shortcut(message, state, number, chat_id)
    elif context == "product_list":
        await handle_product_list_shortcut(message, state, number, chat_id, data)
    elif context == "folder_list":
        await handle_folder_list_shortcut(message, state, number, chat_id, data)
    elif context == "product_detail":
        await handle_product_detail_shortcut(message, state, number, chat_id, data)
    else:
        # Default to main menu
        await handle_main_menu_shortcut(message, state, number, chat_id)


async def handle_main_menu_shortcut(message: Message, state: FSMContext, number: int, chat_id: int):
    """Handle shortcuts in main menu context (1-5)."""
    actions = {
        1: ("add", "main_menu_add"),
        2: ("folders", "main_menu_folders"),
        3: ("list", "main_menu_list"),
        4: ("refresh_all", "main_menu_refresh"),
        5: ("settings", "main_menu_settings"),
    }
    
    if number not in actions:
        track_error_event(chat_id, "invalid_shortcut", "main_menu")
        await message.answer("❌ Invalid option. Use 1-5 for main menu.", reply_markup=main_menu())
        return
    
    action, event_type = actions[number]
    track_navigation_event(chat_id, event_type, shortcut_used=True, context="main_menu")
    
    if action == "add":
        folders = list_folders(chat_id)
        folders_with_count = []
        for f in folders:
            count = count_products_in_folder(f["id"])
            folders_with_count.append({**dict(f), "count": count})
        
        await state.set_state(AddTracking.choosing_folder)
        await state.update_data(nav_context="folder_selection")
        await message.answer(
            "✓ Selected: Add Product\n\n📂 <b>Choose a folder for this product:</b>",
            reply_markup=choose_folder_menu(folders_with_count)
        )
    
    elif action == "folders":
        folders = list_folders(chat_id)
        folders_with_count = []
        for f in folders:
            count = count_products_in_folder(f["id"])
            folders_with_count.append({**dict(f), "count": count})
        
        uncategorized_count = len(list_products_without_folder(chat_id))
        text = f"✓ Selected: My Folders\n\n📂 <b>Your Folders</b>\n\n"
        if folders_with_count:
            text += f"You have {len(folders_with_count)} folder(s)\n"
        text += f"📦 Uncategorized: {uncategorized_count} product(s)"
        
        await state.update_data(nav_context="folder_list", folders_data=folders_with_count, page=1)
        await message.answer(text, reply_markup=folders_list_menu(folders_with_count))
    
    elif action == "list":
        rows = list_products(chat_id)
        if not rows:
            await message.answer("📦 No products tracked yet.\n\nAdd your first product!", reply_markup=main_menu())
        else:
            total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
            header = f"✓ Selected: All Products\n\n📦 <b>All Tracked Products</b> ({len(rows)})\n💰 Total: £{total:,.2f}"
            await state.update_data(nav_context="product_list", products_data=rows, page=1)
            await message.answer(header, reply_markup=products_list(rows, show_folder=True))
    
    elif action == "refresh_all":
        await message.answer("✓ Selected: Refresh All\n\n🔄 Refreshing all prices...")
        await refresh_prices_once()
        await message.answer("✅ All prices refreshed!", reply_markup=main_menu())
    
    elif action == "settings":
        await message.answer(
            "✓ Selected: Settings\n\n⚙️ <b>Settings</b>\n\n"
            "Settings panel coming soon in Phase 2!\n"
            "Future features:\n"
            "• Custom price thresholds\n"
            "• Notification preferences\n"
            "• Export/Import data",
            reply_markup=main_menu()
        )


async def handle_product_list_shortcut(message: Message, state: FSMContext, number: int, chat_id: int, data: dict):
    """Handle shortcuts in product list context (1-9)."""
    products_data = data.get("products_data", [])
    page = data.get("page", 1)
    
    # Calculate which product based on page
    page_size = 9
    start = (page - 1) * page_size
    index = start + number - 1
    
    if index >= len(products_data):
        track_error_event(chat_id, "out_of_range_shortcut", "product_list")
        await message.answer(f"❌ Invalid option. Only {len(products_data) - start} products on this page.")
        return
    
    row = products_data[index]
    track_navigation_event(chat_id, "product_view", shortcut_used=True, context="product_list")
    
    # Show product detail
    if ("title" not in row.keys()) or (row["title"] is None) or (not str(row["title"]).strip()):
        t = await fetch_title(row["url"], session=_http_session)
        if t:
            update_title(row["id"], t)
            row = get_product(row["id"])
    
    await state.update_data(nav_context="product_detail", current_product=row, page=page)
    await message.answer(
        f"✓ Selected: Product {number}\n\n{product_text(row)}",
        reply_markup=product_detail(row, page)
    )
    
    # Send graph
    hist = list_price_history(row["id"])
    png = build_price_graph(hist, f"{row['asin']} history")
    sent = await message.answer_photo(BufferedInputFile(png, filename=f"{row['asin']}.png"))
    update_graph_message(row["id"], sent.message_id)


async def handle_folder_list_shortcut(message: Message, state: FSMContext, number: int, chat_id: int, data: dict):
    """Handle shortcuts in folder list context (1-9)."""
    folders_data = data.get("folders_data", [])
    page = data.get("page", 1)
    
    # Calculate which folder based on page
    page_size = 9
    start = (page - 1) * page_size
    index = start + number - 1
    
    if index >= len(folders_data):
        track_error_event(chat_id, "out_of_range_shortcut", "folder_list")
        await message.answer(f"❌ Invalid option. Only {len(folders_data) - start} folders on this page.")
        return
    
    folder = folders_data[index]
    folder_id = folder["id"]
    track_navigation_event(chat_id, "folder_view", shortcut_used=True, context="folder_list")
    
    # View folder contents
    rows = list_products(chat_id, folder_id)
    emoji = folder.get("emoji", "📁")
    title = f"{emoji} <b>{folder['name']}</b>"
    
    if not rows:
        text = f"✓ Selected: Folder {number}\n\n{title}\n\nNo products in this folder."
        await message.answer(text, reply_markup=folder_detail_menu(folder_id))
    else:
        total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
        text = f"✓ Selected: Folder {number}\n\n{title}\n💰 Total: £{total:,.2f}\n\n{len(rows)} product(s):"
        await state.update_data(nav_context="product_list", products_data=rows, page=1)
        await message.answer(text, reply_markup=products_list(rows))


async def handle_product_detail_shortcut(message: Message, state: FSMContext, number: int, chat_id: int, data: dict):
    """Handle shortcuts in product detail context (1-4)."""
    current_product = data.get("current_product")
    page = data.get("page", 1)
    
    if not current_product:
        track_error_event(chat_id, "missing_context", "product_detail")
        await message.answer("❌ Product context lost. Please select a product again.", reply_markup=main_menu())
        return
    
    actions = {
        1: "toggle",
        2: "refresh",
        3: "move",
        4: "remove",
    }
    
    if number not in actions:
        track_error_event(chat_id, "invalid_shortcut", "product_detail")
        await message.answer("❌ Invalid option. Use 1-4 for product actions.")
        return
    
    action = actions[number]
    product_id = current_product["id"]
    track_navigation_event(chat_id, f"product_{action}", shortcut_used=True, context="product_detail")
    
    if action == "toggle":
        toggle_alerts(product_id)
        row = get_product(product_id)
        status = "enabled" if row["alerts_enabled"] else "disabled"
        await message.answer(
            f"✓ Alerts {status}\n\n{product_text(row)}",
            reply_markup=product_detail(row, page)
        )
    
    elif action == "refresh":
        row = get_product(product_id)
        await message.answer("✓ Refreshing price...")
        price = await fetch_price(row["url"], session=_http_session)
        if price is not None and price != row["last_price"]:
            update_price(row["id"], price)
            row = get_product(row["id"])
        
        await message.answer(product_text(row), reply_markup=product_detail(row, page))
        
        # Update graph
        hist = list_price_history(row["id"])
        png = build_price_graph(hist, f"{row['asin']} history")
        sent = await message.answer_photo(BufferedInputFile(png, filename=f"{row['asin']}.png"))
        
        if row["last_graph_message_id"]:
            try:
                await bot.delete_message(chat_id, row["last_graph_message_id"])
            except Exception:
                pass
        update_graph_message(row["id"], sent.message_id)
    
    elif action == "move":
        folders = list_folders(chat_id)
        folders_with_count = []
        for f in folders:
            count = count_products_in_folder(f["id"])
            folders_with_count.append({**dict(f), "count": count})
        
        await state.update_data(moving_product_id=row["id"])
        await message.answer(
            f"✓ Move to Folder\n\n📂 <b>Move {row['asin']}</b>\n\nSelect destination folder:",
            reply_markup=choose_folder_menu(folders_with_count, include_none=True)
        )
    
    elif action == "remove":
        asin = current_product['asin']
        if current_product.get("last_graph_message_id"):
            try:
                await bot.delete_message(chat_id, current_product["last_graph_message_id"])
            except Exception:
                pass
        
        remove_product(product_id)
        await state.clear()
        await message.answer(
            f"✓ Removed\n\n🗑️ <b>{asin}</b> removed from tracking.",
            reply_markup=main_menu()
        )


# ================= Core Handlers =================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if settings.primary_chat_id and message.chat.id != settings.primary_chat_id:
        await message.answer("Bot restricted to a specific chat.")
        return
    await state.clear()
    await state.update_data(nav_context="main_menu")
    await message.answer(
        "🛒 <b>Amazon Price Monitor Bot</b>\n\n"
        "Track Amazon prices and get notified when they change!\n"
        "Organize your products in folders for better management.\n\n"
        "💡 <b>Tip:</b> Press 1-5 for quick actions!\n\n"
        "Select an option below:",
        reply_markup=main_menu()
    )
    track_navigation_event(message.chat.id, "bot_start", shortcut_used=False, context="main_menu")


@router.callback_query(MenuActions.filter(F.action == "add"))
async def cb_add_tracking(callback: CallbackQuery, state: FSMContext):
    # Show folder selection first
    folders = list_folders(callback.message.chat.id)
    folders_with_count = []
    for f in folders:
        count = count_products_in_folder(f["id"])
        folders_with_count.append({**dict(f), "count": count})
    
    await state.set_state(AddTracking.choosing_folder)
    await safe_edit(
        callback.message,
        "📂 <b>Choose a folder for this product:</b>",
        reply_markup=choose_folder_menu(folders_with_count)
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "folders"))
async def cb_folders(callback: CallbackQuery, state: FSMContext):
    folders = list_folders(callback.message.chat.id)
    folders_with_count = []
    for f in folders:
        count = count_products_in_folder(f["id"])
        folders_with_count.append({**dict(f), "count": count})
    
    # Add count for uncategorized
    uncategorized_count = len(list_products_without_folder(callback.message.chat.id))
    
    text = f"📂 <b>Your Folders</b>\n\n"
    if folders_with_count:
        text += f"You have {len(folders_with_count)} folder(s)\n"
    text += f"📦 Uncategorized: {uncategorized_count} product(s)\n\n"
    text += "💡 <b>Tip:</b> Press 1-9 to select a folder!"
    
    await state.update_data(nav_context="folder_list", folders_data=folders_with_count, page=1)
    await safe_edit(callback.message, text, reply_markup=folders_list_menu(folders_with_count))
    await safe_answer(callback)
    track_navigation_event(callback.message.chat.id, "folders_view", shortcut_used=False, context="main_menu")


@router.callback_query(MenuActions.filter(F.action == "list"))
async def cb_list(callback: CallbackQuery, state: FSMContext, callback_data: MenuActions):
    rows = list_products(callback.message.chat.id)
    page = callback_data.page
    
    if not rows:
        await safe_edit(callback.message, "📦 No products tracked yet.\n\nAdd your first product!", reply_markup=main_menu())
    else:
        total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
        header = f"📦 <b>All Tracked Products</b> ({len(rows)})\n💰 Total: £{total:,.2f}\n\n💡 <b>Tip:</b> Press 1-9 to view a product!"
        await state.update_data(nav_context="product_list", products_data=rows, page=page)
        await safe_edit(callback.message, header, products_list(rows, show_folder=True, page=page))
    await safe_answer(callback)
    track_navigation_event(callback.message.chat.id, "products_list", shortcut_used=False, context="main_menu")


@router.callback_query(MenuActions.filter(F.action == "back"))
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(nav_context="main_menu")
    await safe_edit(callback.message, "Main menu:\n\n💡 <b>Tip:</b> Press 1-5 for quick actions!", main_menu())
    await safe_answer(callback)
    track_navigation_event(callback.message.chat.id, "back_to_main", shortcut_used=False)


@router.callback_query(MenuActions.filter(F.action == "refresh_all"))
async def cb_refresh_all(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback, "🔄 Refreshing all prices...", show_alert=True)
    await refresh_prices_once()
    await state.update_data(nav_context="main_menu")
    await safe_edit(callback.message, "✅ All prices refreshed!", main_menu())
    track_navigation_event(callback.message.chat.id, "refresh_all", shortcut_used=False, context="main_menu")

@router.callback_query(MenuActions.filter(F.action == "settings"))
async def cb_settings(callback: CallbackQuery, state: FSMContext):
    await state.update_data(nav_context="main_menu")
    await safe_edit(
        callback.message,
        "⚙️ <b>Settings</b>\n\n"
        "Settings panel coming soon in Phase 2!\n"
        "Future features:\n"
        "• Custom price thresholds\n"
        "• Notification preferences\n"
        "• Export/Import data",
        reply_markup=main_menu()
    )
    await safe_answer(callback)
    track_navigation_event(callback.message.chat.id, "settings_view", shortcut_used=False, context="main_menu")

@router.callback_query(MenuActions.filter(F.action == "noop"))
async def cb_noop(callback: CallbackQuery):
    """No-op handler for non-clickable buttons like page indicators."""
    await safe_answer(callback)


@router.callback_query(ProductActions.filter(F.action == "back_to_list"))
async def cb_back_to_list(callback: CallbackQuery, callback_data: ProductActions):
    """Go back to product list and delete the graph."""
    row = get_product(callback_data.id)
    if row and row["last_graph_message_id"]:
        try:
            await bot.delete_message(callback.message.chat.id, row["last_graph_message_id"])
            update_graph_message(callback_data.id, None)
        except Exception:
            pass
    
    # Show product list
    rows = list_products(callback.message.chat.id)
    if not rows:
        await safe_edit(callback.message, "📦 No products tracked yet.\n\nAdd your first product!", reply_markup=main_menu())
    else:
        total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
        header = f"📦 <b>All Tracked Products</b> ({len(rows)})\n💰 Total: £{total:,.2f}"
        await safe_edit(callback.message, header, products_list(rows, show_folder=True))
    await safe_answer(callback)


# ================= Folder Handlers =================

@router.callback_query(FolderActions.filter(F.action == "select"))
async def cb_folder_select(callback: CallbackQuery, state: FSMContext, callback_data: FolderActions):
    data = await state.get_data()
    moving_product_id = data.get("moving_product_id")
    
    if moving_product_id:
        # This is a move operation
        folder_id = callback_data.id if callback_data.id != 0 else None
        execute("UPDATE products SET folder_id = ? WHERE id = ?", (folder_id, moving_product_id))
        await state.clear()
        
        row = get_product(moving_product_id)
        folder_name = "No Folder" if folder_id is None else get_folder(folder_id)["name"]
        await safe_edit(
            callback.message,
            f"✅ Moved to: <b>{folder_name}</b>\n\n" + product_text(row),
            reply_markup=product_detail(row)
        )
        await safe_answer(callback, f"Moved to {folder_name}")
    else:
        # This is adding a new product - original flow
        folder_id = callback_data.id if callback_data.id != 0 else None
        await state.update_data(folder_id=folder_id)
        await state.set_state(AddTracking.waiting_for_url)
        
        folder_name = "No Folder" if folder_id is None else get_folder(folder_id)["name"]
        await safe_edit(
            callback.message,
            f"📂 Folder: <b>{folder_name}</b>\n\n"
            "Send the Amazon product URL:",
            reply_markup=add_tracking_menu()
        )
        await safe_answer(callback)


@router.callback_query(FolderActions.filter(F.action == "create"))
async def cb_folder_create(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateFolder.waiting_for_name)
    await safe_edit(
        callback.message,
        "📂 <b>Create New Folder</b>\n\n"
        "Send the folder name:",
        reply_markup=add_tracking_menu()
    )
    await safe_answer(callback)


@router.callback_query(FolderActions.filter(F.action == "view"))
async def cb_folder_view(callback: CallbackQuery, callback_data: FolderActions):
    folder_id = callback_data.id if callback_data.id != 0 else None
    
    if folder_id is None:
        rows = list_products_without_folder(callback.message.chat.id)
        title = "📦 Uncategorized Products"
    else:
        rows = list_products(callback.message.chat.id, folder_id)
        folder = get_folder(folder_id)
        emoji = folder["emoji"] if folder and "emoji" in folder.keys() else "📁"
        title = f"{emoji} <b>{folder['name']}</b>"
    
    if not rows:
        text = f"{title}\n\nNo products in this folder."
        await safe_edit(callback.message, text, reply_markup=folder_detail_menu(folder_id or 0))
    else:
        total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
        text = f"{title}\n💰 Total: £{total:,.2f}\n\n{len(rows)} product(s):"
        await safe_edit(callback.message, text, reply_markup=products_list(rows))
    
    await safe_answer(callback)


@router.callback_query(FolderActions.filter(F.action == "delete"))
async def cb_folder_delete(callback: CallbackQuery, callback_data: FolderActions):
    folder_id = callback_data.id
    folder = get_folder(folder_id)
    if folder:
        delete_folder(folder_id)
        await safe_answer(callback, f"Folder '{folder['name']}' deleted!", show_alert=True)
        await cb_folders(callback)
    else:
        await safe_answer(callback, "Folder not found", show_alert=True)


@router.message(CreateFolder.waiting_for_name)
async def create_folder_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) > 50:
        await message.answer("❌ Folder name too long (max 50 characters)")
        return
    
    await state.update_data(folder_name=name)
    await state.set_state(CreateFolder.waiting_for_emoji)
    await message.answer(
        f"📂 Folder: <b>{name}</b>\n\n"
        "Send an emoji for this folder (or /skip):",
        reply_markup=add_tracking_menu()
    )


@router.message(CreateFolder.waiting_for_emoji)
async def create_folder_emoji(message: Message, state: FSMContext):
    emoji = "📁"
    if message.text and message.text != "/skip":
        emoji = message.text.strip()[0] if message.text.strip() else "📁"
    
    data = await state.get_data()
    folder_name = data.get("folder_name")
    
    try:
        folder_id = create_folder(folder_name, message.chat.id, emoji)
        await state.clear()
        await message.answer(
            f"✅ Folder created: {emoji} <b>{folder_name}</b>",
            reply_markup=main_menu()
        )
    except Exception as e:
        await message.answer(f"❌ Error creating folder (maybe duplicate name?)\n{str(e)}")
        await state.clear()


# ================= Product Tracking Handlers =================

@router.message(AddTracking.waiting_for_url)
async def add_tracking_url(message: Message, state: FSMContext):
    url = message.text.strip()
    # Normalize URL (allow user to paste without protocol)
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    valid_host = host and ("amazon" in host or host.startswith("amzn."))
    if not valid_host:
        await message.answer("Invalid URL. Expected an Amazon domain (e.g. amazon.co.uk or amzn.eu short link).", reply_markup=add_tracking_menu())
        return
    
    # Get folder from state
    data = await state.get_data()
    folder_id = data.get("folder_id")
    
    # Resolve short links (amzn.eu / amzn.to etc.) to full URL before extracting ASIN
    asin = await resolve_asin(url, session=_http_session)
    pid = add_product(asin, url, message.chat.id, folder_id)
    price = await fetch_price(url, session=_http_session)
    if price is not None:
        update_price(pid, price)
    title = await fetch_title(url, session=_http_session)
    if title:
        update_title(pid, title)
    row = get_product(pid)
    
    folder_info = ""
    if folder_id:
        folder = get_folder(folder_id)
        emoji = folder["emoji"] if folder and "emoji" in folder.keys() else "📁"
        folder_info = f"\n📂 {emoji} {folder['name']}"
    
    price_str = f"£{price:.2f}" if price is not None else "n/a"
    name_line = f"🧾 <b>{html.escape(title)}</b>\n" if title else ""
    link_line = f"🔗 <a href=\"{row['url']}\">Open on Amazon</a>\n"
    
    await message.answer(
        f"✅ <b>Tracking Started</b>\n\n"
        f"{name_line}"
        f"{link_line}"
        f"🏷️ ASIN: <b>{asin}</b>\n"
        f"💰 Price: <b>{price_str}</b>"
        f"{folder_info}",
        reply_markup=product_detail(row)
    )
    await state.clear()
    
    # Send initial graph
    hist = list_price_history(pid)
    png = build_price_graph(hist, f"{asin} price history")
    sent = await message.answer_photo(BufferedInputFile(png, filename=f"{asin}.png"))
    update_graph_message(pid, sent.message_id)


@router.callback_query(ProductActions.filter(F.action == "show"))
async def cb_show(callback: CallbackQuery, state: FSMContext, callback_data: ProductActions):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    # Ensure we have a title stored
    if ("title" not in row.keys()) or (row["title"] is None) or (not str(row["title"]).strip()):
        t = await fetch_title(row["url"], session=_http_session)
        if t:
            update_title(row["id"], t)
            row = get_product(row["id"])  # refresh with title
    
    page = callback_data.page
    await state.update_data(nav_context="product_detail", current_product=dict(row), page=page)
    await safe_edit(callback.message, product_text(row), reply_markup=product_detail(row, page))
    # send graph separately
    hist = list_price_history(row["id"])
    png = build_price_graph(hist, f"{row['asin']} history")
    sent = await callback.message.answer_photo(BufferedInputFile(png, filename=f"{row['asin']}.png"))
    update_graph_message(row["id"], sent.message_id)
    await safe_answer(callback)
    track_navigation_event(callback.message.chat.id, "product_view", shortcut_used=False, context="product_list")


@router.callback_query(ProductActions.filter(F.action == "toggle"))
async def cb_toggle(callback: CallbackQuery, callback_data: ProductActions):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    new_state = not bool(row["alerts_enabled"])
    toggle_alerts(row["id"], new_state)
    row = get_product(row["id"])  # refetch
    page = callback_data.page
    await safe_edit(callback.message, product_text(row), reply_markup=product_detail(row, page))
    await safe_answer(callback, "Alerts ON" if new_state else "Alerts OFF")
    track_navigation_event(callback.message.chat.id, "product_toggle_alerts", shortcut_used=False, context="product_detail")


@router.callback_query(ProductActions.filter(F.action == "refresh"))
async def cb_refresh(callback: CallbackQuery, callback_data: ProductActions):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    price = await fetch_price(row["url"], session=_http_session)
    if price is not None and price != row["last_price"]:
        update_price(row["id"], price)
        row = get_product(row["id"])  # refetch
    await safe_edit(callback.message, product_text(row), reply_markup=product_detail(row))
    # graph update
    hist = list_price_history(row["id"])
    png = build_price_graph(hist, f"{row['asin']} history")
    sent = await callback.message.answer_photo(BufferedInputFile(png, filename=f"{row['asin']}.png"))
    # delete previous graph
    if row["last_graph_message_id"]:
        try:
            await bot.delete_message(callback.message.chat.id, row["last_graph_message_id"])
        except Exception:
            pass
    update_graph_message(row["id"], sent.message_id)
    await safe_answer(callback, "Refreshed")


@router.callback_query(ProductActions.filter(F.action == "remove"))
async def cb_remove(callback: CallbackQuery, callback_data: ProductActions):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    # delete graph message if any
    if row["last_graph_message_id"]:
        try:
            await bot.delete_message(callback.message.chat.id, row["last_graph_message_id"])
        except Exception:
            pass
    remove_product(row["id"])
    await safe_edit(callback.message, f"🗑️ Removed <b>{row['asin']}</b>", reply_markup=main_menu())
    await safe_answer(callback, "Removed")


@router.callback_query(ProductActions.filter(F.action == "move"))
async def cb_move(callback: CallbackQuery, callback_data: ProductActions, state: FSMContext):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    
    folders = list_folders(callback.message.chat.id)
    folders_with_count = []
    for f in folders:
        count = count_products_in_folder(f["id"])
        folders_with_count.append({**dict(f), "count": count})
    
    await state.update_data(moving_product_id=row["id"])
    await safe_edit(
        callback.message,
        f"📂 <b>Move {row['asin']}</b>\n\nSelect destination folder:",
        reply_markup=choose_folder_menu(folders_with_count, include_none=True)
    )
    await safe_answer(callback)


# ================= Helper =================

def product_text(row) -> str:
    price = row["last_price"]
    price_str = f"£{price:.2f}" if price is not None else "n/a"
    alerts = "🔔 ON" if row["alerts_enabled"] else "🔕 OFF"
    
    folder_info = ""
    # Check if folder_id exists in row keys
    if "folder_id" in row.keys() and row["folder_id"]:
        folder = get_folder(row["folder_id"])
        if folder:
            emoji = folder["emoji"] if "emoji" in folder.keys() else "📁"
            folder_info = f"\n📂 {emoji} {folder['name']}"

    name_line = ""
    if "title" in row.keys() and row["title"]:
        name_line = f"🧾 <b>{html.escape(row['title'])}</b>\n"
    link_line = f"🔗 <a href=\"{row['url']}\">Open on Amazon</a>\n"
    
    return (
        f"{name_line}"
        f"{link_line}"
        f"🏷️ <b>{row['asin']}</b>\n"
        f"💰 Price: <b>{price_str}</b>\n"
        f"🔔 Alerts: <b>{alerts}</b>"
        f"{folder_info}"
    )
# ================= Background Price Refresh =================

async def price_refresh_loop():
    await asyncio.sleep(5)
    while True:
        try:
            await refresh_prices_once()
        except Exception as e:
            print("[ERROR] refresh loop", e)
        await asyncio.sleep(PRICE_REFRESH_MINUTES * 60)


async def refresh_prices_once():
    # If restricted to specific chat, only refresh those products; otherwise refresh all
    rows = list_products(settings.primary_chat_id) if settings.primary_chat_id else list_all_products()
    now = asyncio.get_event_loop().time()
    for row in rows:
        last_t = _rate_limit.get(row["id"], 0)
        if now - last_t < MIN_FETCH_INTERVAL:
            continue
        price = await fetch_price(row["url"], session=_http_session)
        _rate_limit[row["id"]] = now
        if price is None:
            continue
        if row["last_price"] is None or (price is not None and abs(price - row["last_price"]) >= ALERT_MIN_DELTA):
            old = row["last_price"]
            update_price(row["id"], price)
            refreshed = get_product(row["id"])  # updated
            if refreshed["alerts_enabled"] and old is not None and abs(price - old) >= ALERT_MIN_DELTA:
                delta = price - (old or price)
                direction = "↓" if old is not None and price < old else "↑" if old is not None and price > old else "="
                msg = f"<b>Price change</b> {direction}\n{refreshed['asin']}\nOld: {old if old is not None else 'n/a'}\nNew: £{price:.2f}\nΔ: {delta:.2f}"
                await bot.send_message(refreshed["chat_id"], msg)
            # graph
            hist = list_price_history(refreshed["id"])
            png = build_price_graph(hist, f"{refreshed['asin']} history")
            sent = await bot.send_photo(refreshed["chat_id"], BufferedInputFile(png, filename=f"{refreshed['asin']}.png"))
            if refreshed["last_graph_message_id"]:
                try:
                    await bot.delete_message(refreshed["chat_id"], refreshed["last_graph_message_id"])
                except Exception:
                    pass
            update_graph_message(refreshed["id"], sent.message_id)

# ================= Main =================

async def main():
    if not settings.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in environment")
    init_db()
    global _http_session
    _http_session = aiohttp.ClientSession()
    dp = router
    asyncio.create_task(price_refresh_loop())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))
        except NotImplementedError:
            pass
    log.info("bot.start")
    await dp.start_polling(bot)

async def shutdown(sig):
    log.info("bot.shutdown", signal=str(sig))
    if _http_session:
        await _http_session.close()
    await bot.session.close()
    log.info("bot.shutdown.complete")


if __name__ == "__main__":
    asyncio.run(main())
