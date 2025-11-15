import asyncio
import signal
import structlog
import aiohttp
import html
import datetime as dt
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    search_products_by_name,
    search_products_by_asin,
    filter_products_by_price_range,
    get_products_statistics,
    toggle_all_alerts,
    get_price_trends,
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
from .states import AddTracking, CreateFolder, SearchProduct
from .keyboards import (
    main_menu, products_list, product_detail, add_tracking_menu,
    choose_folder_menu, folders_list_menu, folder_detail_menu,
    settings_menu, search_menu, notification_settings_menu,
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


# ================= Core Handlers =================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if settings.primary_chat_id and message.chat.id != settings.primary_chat_id:
        await message.answer("Bot restricted to a specific chat.")
        return
    await state.clear()
    await message.answer(
        "🛒 <b>Amazon Price Monitor Bot</b>\n\n"
        "Welcome to your personal Amazon price tracker! 🎯\n\n"
        "✨ <b>What I can do:</b>\n"
        "• 📊 Track unlimited Amazon products\n"
        "• 🔔 Send price change notifications\n"
        "• 📂 Organize products in folders\n"
        "• 📈 Show price history graphs\n"
        "• 🔍 Search and filter your products\n"
        "• 💾 Export your product list\n\n"
        "👇 Choose an option below to get started:",
        reply_markup=main_menu()
    )


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
async def cb_folders(callback: CallbackQuery):
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
    text += f"📦 Uncategorized: {uncategorized_count} product(s)"
    
    await safe_edit(callback.message, text, reply_markup=folders_list_menu(folders_with_count))
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "list"))
async def cb_list(callback: CallbackQuery):
    rows = list_products(callback.message.chat.id)
    if not rows:
        await safe_edit(callback.message, "📦 No products tracked yet.\n\nAdd your first product!", reply_markup=main_menu())
    else:
        total = sum((r['last_price'] or 0) for r in rows if r['last_price'] is not None)
        header = f"📦 <b>All Tracked Products</b> ({len(rows)})\n💰 Total: £{total:,.2f}"
        await safe_edit(callback.message, header, products_list(rows, show_folder=True))
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "back"))
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(callback.message, "Main menu:", main_menu())
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "refresh_all"))
async def cb_refresh_all(callback: CallbackQuery):
    await safe_answer(callback, "🔄 Refreshing all prices...", show_alert=True)
    await refresh_prices_once()
    await safe_edit(callback.message, "✅ All prices refreshed!", main_menu())


# ================= New Menu Handlers =================

@router.callback_query(MenuActions.filter(F.action == "help"))
async def cb_help(callback: CallbackQuery):
    help_text = (
        "ℹ️ <b>Amazon Price Monitor Bot - Help</b>\n\n"
        "<b>📊 Features:</b>\n"
        "• Track Amazon product prices with automatic updates\n"
        "• Organize products into custom folders\n"
        "• Get notifications when prices change\n"
        "• View price history graphs\n"
        "• Search and filter your products\n"
        "• Export your product list\n\n"
        "<b>🚀 Quick Start:</b>\n"
        "1. Click <b>➕ Add Product</b> to track a new item\n"
        "2. Choose a folder or create one\n"
        "3. Send the Amazon product URL\n"
        "4. Get instant price tracking!\n\n"
        "<b>📂 Folders:</b>\n"
        "Organize your products with custom names and emojis\n\n"
        "<b>🔔 Alerts:</b>\n"
        "Enable/disable price change notifications per product\n\n"
        "<b>📊 Statistics:</b>\n"
        "View overview of all tracked products and trends\n\n"
        "<b>🔍 Search:</b>\n"
        "Find products by name, ASIN, or filter by price\n\n"
        "<b>⚙️ Settings:</b>\n"
        "Manage notifications and export data"
    )
    await safe_edit(callback.message, help_text, reply_markup=main_menu())
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "stats"))
async def cb_stats(callback: CallbackQuery):
    stats = get_products_statistics(callback.message.chat.id)
    trends = get_price_trends(callback.message.chat.id)
    
    stats_text = (
        f"📊 <b>Your Statistics</b>\n\n"
        f"📦 Total Products: <b>{stats['total_products']}</b>\n"
        f"💰 Products with Price: <b>{stats['total_with_price']}</b>\n"
        f"💵 Total Value: <b>£{stats['total_value']:,.2f}</b>\n"
        f"🔔 Alerts Enabled: <b>{stats['alerts_enabled']}</b>\n\n"
    )
    
    if trends["decreased"]:
        stats_text += f"📉 <b>Recent Price Drops ({len(trends['decreased'])}):</b>\n"
        for prod in trends["decreased"][:5]:  # Show top 5
            title = prod['title'][:30] + "..." if prod['title'] and len(prod['title']) > 30 else (prod['title'] or prod['asin'])
            stats_text += f"  • {title} (-£{prod['change']:.2f})\n"
        stats_text += "\n"
    
    if trends["increased"]:
        stats_text += f"📈 <b>Recent Price Increases ({len(trends['increased'])}):</b>\n"
        for prod in trends["increased"][:5]:  # Show top 5
            title = prod['title'][:30] + "..." if prod['title'] and len(prod['title']) > 30 else (prod['title'] or prod['asin'])
            stats_text += f"  • {title} (+£{prod['change']:.2f})\n"
    
    if not trends["decreased"] and not trends["increased"]:
        stats_text += "📊 No recent price changes detected."
    
    await safe_edit(callback.message, stats_text, reply_markup=main_menu())
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "search"))
async def cb_search(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "🔍 <b>Search Products</b>\n\nChoose a search method:",
        reply_markup=search_menu()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "search_name"))
async def cb_search_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchProduct.waiting_for_name)
    await safe_edit(
        callback.message,
        "🔤 <b>Search by Name</b>\n\nEnter product name to search:",
        reply_markup=add_tracking_menu()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "search_asin"))
async def cb_search_asin(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchProduct.waiting_for_asin)
    await safe_edit(
        callback.message,
        "🏷️ <b>Search by ASIN</b>\n\nEnter ASIN to search:",
        reply_markup=add_tracking_menu()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "filter_price"))
async def cb_filter_price(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchProduct.waiting_for_price_range)
    await safe_edit(
        callback.message,
        "💰 <b>Filter by Price</b>\n\nEnter price range (e.g., '10-50' or '0-100'):",
        reply_markup=add_tracking_menu()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "settings"))
async def cb_settings(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        "⚙️ <b>Settings</b>\n\nManage your preferences:",
        reply_markup=settings_menu()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "notification_settings"))
async def cb_notification_settings(callback: CallbackQuery):
    products = list_products(callback.message.chat.id)
    all_enabled = all(p['alerts_enabled'] for p in products) if products else False
    
    text = (
        "🔔 <b>Notification Settings</b>\n\n"
        f"Currently: <b>{'All alerts enabled' if all_enabled else 'Some alerts disabled'}</b>\n\n"
        "Toggle to enable/disable alerts for all products at once."
    )
    
    await safe_edit(
        callback.message,
        text,
        reply_markup=notification_settings_menu(all_enabled)
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "toggle_all_alerts"))
async def cb_toggle_all_alerts(callback: CallbackQuery):
    products = list_products(callback.message.chat.id)
    if not products:
        await safe_answer(callback, "No products to toggle", show_alert=True)
        return
    
    all_enabled = all(p['alerts_enabled'] for p in products)
    new_state = not all_enabled
    
    toggle_all_alerts(callback.message.chat.id, new_state)
    
    await safe_answer(callback, f"All alerts {'enabled' if new_state else 'disabled'}!", show_alert=True)
    await cb_notification_settings(callback)


@router.callback_query(MenuActions.filter(F.action == "export"))
async def cb_export(callback: CallbackQuery):
    products = list_products(callback.message.chat.id)
    
    if not products:
        await safe_answer(callback, "No products to export", show_alert=True)
        await safe_edit(callback.message, "⚙️ Settings", reply_markup=settings_menu())
        return
    
    # Create CSV-like text export
    export_text = "📤 <b>Exported Products List</b>\n\n"
    export_text += f"Total Products: {len(products)}\n"
    export_text += f"Export Date: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    export_text += "─" * 40 + "\n\n"
    
    for i, prod in enumerate(products, 1):
        title = prod['title'] if prod['title'] else "No title"
        price = f"£{prod['last_price']:.2f}" if prod['last_price'] is not None else "n/a"
        folder = prod.get('folder_name', 'Uncategorized')
        alerts = "ON" if prod['alerts_enabled'] else "OFF"
        
        export_text += f"<b>{i}. {html.escape(title[:40])}</b>\n"
        export_text += f"   ASIN: {prod['asin']}\n"
        export_text += f"   Price: {price}\n"
        export_text += f"   Folder: {folder}\n"
        export_text += f"   Alerts: {alerts}\n"
        export_text += f"   URL: {prod['url']}\n\n"
    
    await safe_answer(callback, "Products exported!", show_alert=True)
    await callback.message.answer(export_text)
    await safe_edit(callback.message, "⚙️ Settings", reply_markup=settings_menu())


@router.callback_query(MenuActions.filter(F.action == "clear_data"))
async def cb_clear_data(callback: CallbackQuery):
    # This is a destructive action - show confirmation
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ Confirm Delete All", callback_data=MenuActions(action="confirm_clear").pack())
    b.button(text="❌ Cancel", callback_data=MenuActions(action="settings").pack())
    b.adjust(1)
    
    await safe_edit(
        callback.message,
        "⚠️ <b>Clear All Data</b>\n\n"
        "This will delete ALL tracked products and folders!\n"
        "This action cannot be undone.\n\n"
        "Are you sure?",
        reply_markup=b.as_markup()
    )
    await safe_answer(callback)


@router.callback_query(MenuActions.filter(F.action == "confirm_clear"))
async def cb_confirm_clear(callback: CallbackQuery):
    from app.core.db import execute
    
    # Delete all user's data
    execute("DELETE FROM products WHERE chat_id = ?", (callback.message.chat.id,))
    execute("DELETE FROM folders WHERE chat_id = ?", (callback.message.chat.id,))
    
    await safe_answer(callback, "All data cleared!", show_alert=True)
    await safe_edit(
        callback.message,
        "✅ All data has been cleared.\n\nYou can start fresh by adding new products!",
        reply_markup=main_menu()
    )


# ================= Search Handlers =================

@router.message(SearchProduct.waiting_for_name)
async def search_by_name(message: Message, state: FSMContext):
    search_term = message.text.strip()
    
    if not search_term:
        await message.answer("Please enter a search term.", reply_markup=add_tracking_menu())
        return
    
    results = search_products_by_name(message.chat.id, search_term)
    await state.clear()
    
    if not results:
        await message.answer(
            f"🔍 No products found matching '<b>{html.escape(search_term)}</b>'",
            reply_markup=main_menu()
        )
    else:
        total = sum((r['last_price'] or 0) for r in results if r['last_price'] is not None)
        header = f"🔍 <b>Search Results</b> ({len(results)} found)\n💰 Total: £{total:,.2f}\n\nSearch: '{html.escape(search_term)}'"
        await message.answer(header, reply_markup=products_list(results, show_folder=True))


@router.message(SearchProduct.waiting_for_asin)
async def search_by_asin(message: Message, state: FSMContext):
    asin = message.text.strip().upper()
    
    if not asin:
        await message.answer("Please enter an ASIN.", reply_markup=add_tracking_menu())
        return
    
    results = search_products_by_asin(message.chat.id, asin)
    await state.clear()
    
    if not results:
        await message.answer(
            f"🔍 No products found with ASIN '<b>{asin}</b>'",
            reply_markup=main_menu()
        )
    else:
        total = sum((r['last_price'] or 0) for r in results if r['last_price'] is not None)
        header = f"🔍 <b>Search Results</b> ({len(results)} found)\n💰 Total: £{total:,.2f}\n\nASIN: {asin}"
        await message.answer(header, reply_markup=products_list(results, show_folder=True))


@router.message(SearchProduct.waiting_for_price_range)
async def filter_by_price(message: Message, state: FSMContext):
    price_range = message.text.strip()
    
    # Parse price range (e.g., "10-50" or "0-100")
    try:
        if '-' in price_range:
            min_price, max_price = price_range.split('-')
            min_price = float(min_price.strip())
            max_price = float(max_price.strip())
        else:
            await message.answer("Invalid format. Use format like '10-50'", reply_markup=add_tracking_menu())
            return
    except ValueError:
        await message.answer("Invalid price range. Use numbers only (e.g., '10-50')", reply_markup=add_tracking_menu())
        return
    
    results = filter_products_by_price_range(message.chat.id, min_price, max_price)
    await state.clear()
    
    if not results:
        await message.answer(
            f"🔍 No products found in price range £{min_price:.2f} - £{max_price:.2f}",
            reply_markup=main_menu()
        )
    else:
        total = sum((r['last_price'] or 0) for r in results if r['last_price'] is not None)
        header = f"🔍 <b>Price Filter Results</b> ({len(results)} found)\n💰 Total: £{total:,.2f}\n\nRange: £{min_price:.2f} - £{max_price:.2f}"
        await message.answer(header, reply_markup=products_list(results, show_folder=True))


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
async def cb_show(callback: CallbackQuery, callback_data: ProductActions):
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
    await safe_edit(callback.message, product_text(row), reply_markup=product_detail(row))
    # send graph separately
    hist = list_price_history(row["id"])
    png = build_price_graph(hist, f"{row['asin']} history")
    sent = await callback.message.answer_photo(BufferedInputFile(png, filename=f"{row['asin']}.png"))
    update_graph_message(row["id"], sent.message_id)
    await safe_answer(callback)


@router.callback_query(ProductActions.filter(F.action == "toggle"))
async def cb_toggle(callback: CallbackQuery, callback_data: ProductActions):
    row = get_product(callback_data.id)
    if not row:
        await safe_answer(callback, "Missing", show_alert=True)
        return
    new_state = not bool(row["alerts_enabled"])
    toggle_alerts(row["id"], new_state)
    row = get_product(row["id"])  # refetch
    await safe_edit(callback.message, product_text(row), reply_markup=product_detail(row))
    await safe_answer(callback, "Alerts ON" if new_state else "Alerts OFF")


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
