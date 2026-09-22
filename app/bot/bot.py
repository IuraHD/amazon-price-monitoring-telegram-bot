import asyncio
import contextlib
import html
import logging
import secrets
import sqlite3

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommand, BufferedInputFile, CallbackQuery, Message

from app.core import db, folders, products
from app.core.config import load_settings
from app.core.service import Tracker
from app.utils.fetch import InvalidProduct, ProductLink, create_session, resolve_link
from app.utils.graph import build_price_graph
from .keyboards import keyboard, menu
from .states import Input

log = logging.getLogger(__name__)
ESC = html.escape
HELP = (
    "<b>Amazon UK price tracker</b>\n\n"
    "Track the displayed primary GBP offer. Shipping, vouchers, Prime-only and subscription discounts "
    "are not calculated. Check the Amazon page before buying.\n\n"
    "/start — menu\n/cancel — cancel the current input\n"
    "/quiet 22 8 Europe/London — defer alerts overnight\n/quiet off — disable quiet hours\n\n"
    "Open a product to set a target, choose drops only, set a minimum percentage change, "
    "or enable stock alerts. Muting disables every alert. History and CSV exports cover up to 90 days.\n\n"
    "Manual checks have a 60-second per-product cooldown. Failed checks retain the last recorded price "
    "and display the failure; that price is not a fresh quote."
)


class Access(BaseMiddleware):
    def __init__(self, settings):
        self.settings = settings

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        if not isinstance(message, Message):
            return
        chat = message.chat
        allowed = self.settings.primary_chat_id is None or chat.id == self.settings.primary_chat_id
        if chat.type != "private":
            allowed = allowed and self.settings.primary_chat_id == chat.id
            if allowed:
                member = await data["bot"].get_chat_member(chat.id, event.from_user.id)
                allowed = member.status in {"creator", "administrator"}
        if not allowed:
            if isinstance(event, CallbackQuery):
                await event.answer("This chat or user is not authorized.", show_alert=True)
            else:
                await event.answer(
                    "This bot is available in private chats, or to administrators of its configured group."
                )
            return
        return await handler(event, data)


async def reset_input(state):
    data = await state.get_data()
    preserved = {
        k: data[k] for k in ("view_folder", "view_page", "query", "sort", "graph_message_id") if k in data
    }
    await state.clear()
    await state.set_data(preserved)


async def remove_graph(message, state):
    data = await state.get_data()
    if data.get("graph_message_id"):
        with contextlib.suppress(TelegramBadRequest):
            await message.bot.delete_message(message.chat.id, data["graph_message_id"])
        await state.update_data(graph_message_id=None)


async def render(message, text, markup, edit=True):
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            if "message to edit not found" not in str(exc) and "message can't be edited" not in str(exc):
                raise
    await message.answer(text, reply_markup=markup)


def product_text(row):
    title = ESC(row["title"] or row["asin"])
    text = (
        f"<b>{title}</b>\nRecorded price: <b>{products.money(row['price_pence'])}</b>\n"
        f"Status: {ESC(row['status'])}\nLast successful check: {ESC(row['last_success'] or 'never')}\n"
        f"Last attempt: {ESC(row['last_attempt'] or 'never')}\n"
    )
    if row["detail"]:
        text += ESC(row["detail"]) + "\n"
    if row["folder_name"]:
        text += f"Folder: {ESC(row['folder_emoji'])} {ESC(row['folder_name'])}\n"
    if row["seller"]:
        text += "Seller: " + ESC(row["seller"]) + "\n"
    text += f"Stock: {ESC(row['availability'])}\n"
    text += f"Alerts: {'on' if row['alerts_enabled'] else 'muted'} · {row['alert_mode']}"
    if row["alert_mode"] == "target":
        text += " " + products.money(row["target_pence"])
    text += f" · minimum change {row['percent']}%\n"
    text += f"Stock alerts: {'on' if row['stock_alert'] else 'off'}\n"
    text += f'<a href="https://www.amazon.co.uk/dp/{ESC(row["asin"], quote=True)}">Open on Amazon UK</a>'
    return text


def product_keyboard(row):
    sid = row["id"]
    return keyboard(
        [("Refresh", f"refresh:{sid}"), ("Set target", f"target:{sid}")],
        [
            ("Unmute" if not row["alerts_enabled"] else "Mute", f"mute:{sid}"),
            ("Alert settings", f"alerts:{sid}"),
        ],
        [("7 days", f"history:{sid}:7"), ("30 days", f"history:{sid}:30"), ("90 days", f"history:{sid}:90")],
        [("Export CSV", f"export:{sid}"), ("Move", f"move:{sid}")],
        [("Remove", f"remove:{sid}"), ("Back to list", "backlist")],
    )


async def show_product(message, state, sid, note="", edit=True):
    row = products.get_product(message.chat.id, sid)
    if not row:
        raise ValueError("This product no longer exists in your chat.")
    await render(
        message, (ESC(note) + "\n\n" if note else "") + product_text(row), product_keyboard(row), edit
    )


async def show_list(message, state, folder=None, page=None, edit=True):
    data = await state.get_data()
    folder = str(folder if folder is not None else data.get("view_folder", "all"))
    page = page if page is not None else data.get("view_page", 0)
    name = "All products" if folder == "all" else "Uncategorized"
    if folder not in {"all", "0"}:
        f = folders.get_folder(message.chat.id, int(folder))
        if not f:
            raise ValueError("Folder no longer exists.")
        name = f["name"]
    rows, total, page = products.list_products(
        message.chat.id, folder, data.get("query", ""), data.get("sort", "recent"), page
    )
    await state.update_data(view_folder=folder, view_page=page)
    buttons = []
    for row in rows:
        title = (row["title"] or row["asin"])[:38]
        marker = "" if row["status"] == "ok" else " · needs check"
        buttons.append([(f"{title} · {products.money(row['price_pence'])}{marker}", f"p:{row['id']}")])
    nav = []
    if page:
        nav.append(("Previous", f"list:{folder}:{page - 1}"))
    if (page + 1) * 8 < total:
        nav.append(("Next", f"list:{folder}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([("Search", "search"), ("Sort", "sort")])
    if data.get("query"):
        buttons.append([("Clear search", "clearsearch")])
    if folder not in {"all", "0"}:
        buttons.extend(
            [
                [("Rename folder", f"rename:{folder}"), ("Change emoji", f"emoji:{folder}")],
                [("Delete folder", f"delfolder:{folder}")],
            ]
        )
    buttons.append([("Add product", "add"), ("Folders", "folders:0"), ("Menu", "home")])
    query = "\nSearch: " + ESC(data["query"]) if data.get("query") else ""
    text = (
        f"<b>{ESC(name)}</b>\n{total} product(s) · page {page + 1} · {ESC(data.get('sort', 'recent'))}{query}"
    )
    summary = products.list_summary(message.chat.id, folder, data.get("query", ""))
    text += f"\nRecorded-price total: {products.money(summary['total_pence'])}"
    text += (
        f"\nUnknown prices excluded: {summary['unknown']} · items needing a check: {summary['needs_check']}"
    )
    if not rows:
        text += "\nNo matching products."
    await render(message, text, keyboard(*buttons), edit)


async def show_folders(message, state, page=0, choose=False, edit=True):
    items = folders.list_folders(message.chat.id)
    page = max(0, min(page, max(0, (len(items) - 1) // 8)))
    data = await state.get_data()
    token = data.get("op", "")
    buttons = [
        [
            (
                f"{f['emoji']} {f['name']} ({f['count']})",
                f"select:{token}:{f['id']}" if choose else f"list:{f['id']}:0",
            )
        ]
        for f in items[page * 8 : (page + 1) * 8]
    ]
    nav = []
    prefix = f"choose:{token}" if choose else "folders"
    if page:
        nav.append(("Previous", f"{prefix}:{page - 1}"))
    if (page + 1) * 8 < len(items):
        nav.append(("Next", f"{prefix}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [("No folder" if choose else "Uncategorized", f"select:{token}:0" if choose else "list:0:0")]
    )
    buttons.append([("Create folder", f"newfolder:{token}" if choose else "newfolder")])
    buttons.append([("Cancel" if choose else "Menu", "home")])
    await render(message, "Choose a folder:" if choose else "<b>Folders</b>", keyboard(*buttons), edit)


def operation(data, token):
    if not token or data.get("op") != token:
        raise ValueError("That operation has expired. Start it again from the menu.")


async def finish_selection(message, state, folder_id, tracker, edit=True):
    data = await state.get_data()
    if data.get("moving"):
        sid = data["moving"]
        products.move_product(message.chat.id, sid, folder_id)
        await reset_input(state)
        await show_product(message, state, sid, "Product moved.", edit)
    elif data.get("link"):
        link = ProductLink(**data["link"])
        sid, created = products.add_product(message.chat.id, link, folder_id)
        await reset_input(state)
        row = products.get_product(message.chat.id, sid)
        result = await tracker.refresh(row["product_id"], manual=True) if created else "skipped"
        note = (
            f"Tracking added. Check: {result}."
            if created
            else "Already tracked. Its existing folder and alert settings were kept; use Move to change folders."
        )
        await show_product(message, state, sid, note, edit)
    else:
        raise ValueError("Start Add or Move again.")


def make_dispatcher(settings, tracker):
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    access = Access(settings)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)

    @dp.message(CommandStart())
    @dp.message(Command("cancel"))
    async def start(message: Message, state: FSMContext):
        await remove_graph(message, state)
        await state.clear()
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO preferences(chat_id,blocked) VALUES(?,0) ON CONFLICT(chat_id) DO UPDATE SET blocked=0",
                (message.chat.id,),
            )
        await message.answer("<b>Amazon UK price tracker</b>\nChoose an action.", reply_markup=menu())

    @dp.message(Command("quiet"))
    async def quiet(message: Message):
        parts = (message.text or "").split()
        try:
            if len(parts) == 2 and parts[1] == "off":
                products.set_quiet_hours(message.chat.id)
                await message.answer("Quiet hours disabled.")
            elif len(parts) in {3, 4}:
                products.set_quiet_hours(
                    message.chat.id,
                    int(parts[1]),
                    int(parts[2]),
                    parts[3] if len(parts) == 4 else "Europe/London",
                )
                await message.answer("Quiet hours saved. Alerts will wait until quiet hours end.")
            else:
                raise ValueError("Use /quiet 22 8 Europe/London or /quiet off.")
        except ValueError as exc:
            await message.answer(ESC(str(exc)))

    @dp.callback_query()
    async def callback(event: CallbackQuery, state: FSMContext):
        # Acknowledge before any fetching, rendering, or database work.
        await event.answer()
        message = event.message
        parts = (event.data or "").split(":")
        action = parts[0]
        data = await state.get_data()
        try:
            if action not in {"history", "export"}:
                await remove_graph(message, state)
            if action == "home":
                await state.clear()
                await render(message, "<b>Amazon UK price tracker</b>", menu())
            elif action == "help":
                await reset_input(state)
                pref = db.fetch_one("SELECT * FROM preferences WHERE chat_id=?", (message.chat.id,))
                hours = (
                    f"\n\nQuiet hours: {pref['quiet_start']}:00–{pref['quiet_end']}:00 {ESC(pref['timezone'])}"
                    if pref and pref["quiet_start"] is not None
                    else "\n\nQuiet hours: off"
                )
                await render(message, HELP + hours, keyboard([("Menu", "home")]))
            elif action in {"list", "backlist", "sort", "clearsearch"}:
                await reset_input(state)
                if action == "sort":
                    sorts = ["recent", "name", "price"]
                    await state.update_data(
                        sort=sorts[(sorts.index(data.get("sort", "recent")) + 1) % 3], view_page=0
                    )
                if action == "clearsearch":
                    await state.update_data(query="", view_page=0)
                await show_list(
                    message,
                    state,
                    parts[1] if action == "list" else None,
                    int(parts[2]) if action == "list" else None,
                )
            elif action == "folders":
                await reset_input(state)
                await show_folders(message, state, int(parts[1]))
            elif action == "search":
                await reset_input(state)
                await state.set_state(Input.search)
                await render(
                    message,
                    "Send a product name or ASIN to search this list.",
                    keyboard([("Cancel", "backlist")]),
                )
            elif action == "add":
                await reset_input(state)
                await state.update_data(op=secrets.token_hex(4))
                await state.set_state(Input.url)
                await render(
                    message,
                    "Send an Amazon product URL. Prices are tracked on Amazon UK in GBP.",
                    keyboard([("Cancel", "home")]),
                )
            elif action == "uk":
                operation(data, parts[1])
                await show_folders(message, state, choose=True)
            elif action == "choose":
                operation(data, parts[1])
                await show_folders(message, state, int(parts[2]), choose=True)
            elif action == "select":
                operation(data, parts[1])
                await finish_selection(message, state, int(parts[2]) or None, tracker)
            elif action == "newfolder":
                if len(parts) > 1:
                    operation(data, parts[1])
                else:
                    await reset_input(state)
                    await state.update_data(op=secrets.token_hex(4))
                await state.set_state(Input.folder_name)
                await render(message, "Send a folder name (1–50 characters).", keyboard([("Cancel", "home")]))
            elif action in {"rename", "emoji", "delfolder"}:
                fid = int(parts[1])
                folder = folders.get_folder(message.chat.id, fid)
                if not folder:
                    raise ValueError("Folder no longer exists.")
                await reset_input(state)
                token = secrets.token_hex(4)
                await state.update_data(edit_folder=fid, op=token)
                if action == "delfolder":
                    await state.update_data(confirm="folder")
                    await render(
                        message,
                        f"Delete <b>{ESC(folder['name'])}</b>? Products will move to Uncategorized.",
                        keyboard([("Delete folder", f"confirm:{token}")], [("Cancel", f"list:{fid}:0")]),
                    )
                else:
                    await state.set_state(Input.rename if action == "rename" else Input.emoji)
                    await render(
                        message,
                        "Send the new folder name." if action == "rename" else "Send one emoji.",
                        keyboard([("Cancel", f"list:{fid}:0")]),
                    )
            elif action == "confirm":
                operation(data, parts[1])
                if data.get("confirm") == "folder":
                    folders.delete_folder(message.chat.id, data["edit_folder"])
                    await reset_input(state)
                    await show_folders(message, state)
                elif data.get("confirm") == "product":
                    products.remove_product(message.chat.id, data["sid"])
                    await reset_input(state)
                    await show_list(message, state)
                else:
                    raise ValueError("Confirmation expired.")
            elif action == "refresh_all":
                await reset_input(state)
                await render(message, "Checking your products…", keyboard([("Menu", "home")]))
                results = await tracker.refresh_chat(message.chat.id)
                await render(
                    message,
                    "Check completed:\n"
                    + "\n".join(f"{key.title()}: {value}" for key, value in results.items())
                    + "\nSkipped means another check is running or the cooldown is active.",
                    menu(),
                )
            elif action in {
                "p",
                "refresh",
                "mute",
                "alerts",
                "mode",
                "stock",
                "target",
                "percent",
                "move",
                "remove",
                "history",
                "export",
            }:
                sid = int(parts[1])
                row = products.get_product(message.chat.id, sid)
                if not row:
                    raise ValueError("This product no longer exists in your chat.")
                await reset_input(state)
                if action == "move":
                    await state.update_data(moving=sid, op=secrets.token_hex(4))
                    await show_folders(message, state, choose=True)
                elif action == "remove":
                    token = secrets.token_hex(4)
                    await state.update_data(op=token, sid=sid, confirm="product")
                    await render(
                        message,
                        "Remove this product from your tracking? Your alerts will be removed. History is deleted if no other chat tracks the product.",
                        keyboard([("Remove product", f"confirm:{token}")], [("Cancel", f"p:{sid}")]),
                    )
                elif action in {"target", "percent"}:
                    await state.update_data(sid=sid)
                    await state.set_state(Input.target if action == "target" else Input.percentage)
                    await render(
                        message,
                        "Send a target price in GBP, such as 29.99."
                        if action == "target"
                        else "Send the minimum change percentage (0–100). Zero allows every qualifying change.",
                        keyboard([("Cancel", f"p:{sid}")]),
                    )
                elif action in {"alerts", "mode", "stock"}:
                    if action == "mode":
                        products.set_alerts(message.chat.id, sid, mode=parts[2])
                    if action == "stock":
                        products.set_alerts(message.chat.id, sid, stock=not row["stock_alert"])
                    row = products.get_product(message.chat.id, sid)
                    await render(
                        message,
                        product_text(row),
                        keyboard(
                            [("Any change", f"mode:{sid}:any"), ("Drops only", f"mode:{sid}:drop")],
                            [("Set target", f"target:{sid}"), ("Minimum %", f"percent:{sid}")],
                            [("Stock alerts: " + ("on" if row["stock_alert"] else "off"), f"stock:{sid}")],
                            [("Back", f"p:{sid}")],
                        ),
                    )
                elif action == "export":
                    payload = products.export_csv(message.chat.id, sid)
                    await message.answer_document(
                        BufferedInputFile(payload, filename=f"{row['asin']}-90-days.csv")
                    )
                elif action == "history":
                    days = int(parts[2])
                    hist = products.history(message.chat.id, sid, days, limit=200)
                    png = await asyncio.to_thread(build_price_graph, hist, row["title"] or row["asin"])
                    if not png:
                        await show_product(message, state, sid, "No observations in this date range yet.")
                    else:
                        await remove_graph(message, state)
                        low = min(r["price_pence"] for r in hist)
                        high = max(r["price_pence"] for r in hist)
                        caption = f"{days} days · min {products.money(low)} · max {products.money(high)} · latest sample {hist[-1]['ts']}. Legacy samples are unverified; see CSV source column."
                        sent = await message.answer_photo(
                            BufferedInputFile(png, filename="history.png"), caption=caption
                        )
                        await state.update_data(graph_message_id=sent.message_id)
                else:
                    note = ""
                    if action == "refresh":
                        await render(message, "Checking this product…", keyboard([("Back", f"p:{sid}")]))
                        result = await tracker.refresh(row["product_id"], manual=True)
                        note = (
                            "Check: "
                            + result
                            + (
                                ". Another check is running or the 60-second cooldown is active."
                                if result == "skipped"
                                else "."
                            )
                        )
                    if action == "mute":
                        products.set_alerts(message.chat.id, sid, enabled=not row["alerts_enabled"])
                    await show_product(message, state, sid, note)
            else:
                await render(message, "This menu is from an older version. Use the current menu.", menu())
        except (ValueError, IndexError) as exc:
            await message.answer(
                ESC(str(exc)) if isinstance(exc, ValueError) else "That button is no longer valid.",
                reply_markup=menu(),
            )
        except sqlite3.IntegrityError:
            await message.answer("A folder with that name already exists. Choose another name.")
        except Exception:
            log.exception("Callback failed: action=%s", action)
            await message.answer(
                "That action failed. Your saved tracking is retained; try again from /start."
            )

    @dp.message()
    async def input_message(message: Message, state: FSMContext):
        current = await state.get_state()
        if not message.text:
            await message.answer("Please send text for this step, or /cancel.")
            return
        text = message.text.strip()
        data = await state.get_data()
        try:
            if current == Input.url.state:
                link = await resolve_link(text, tracker.session)
                await state.update_data(
                    link={"asin": link.asin, "url": link.url, "converted": link.converted}
                )
                await state.set_state(None)
                if link.converted:
                    await message.answer(
                        "This link is from another marketplace. Track the same ASIN on Amazon UK in GBP instead?",
                        reply_markup=keyboard(
                            [("Track on Amazon UK", f"uk:{data['op']}")], [("Cancel", "home")]
                        ),
                    )
                else:
                    await show_folders(message, state, choose=True, edit=False)
            elif current == Input.folder_name.state:
                await state.update_data(folder_name=folders.clean_name(text))
                await state.set_state(Input.folder_emoji)
                await message.answer(
                    "Send one emoji, or /skip for the default.", reply_markup=keyboard([("Cancel", "home")])
                )
            elif current == Input.folder_emoji.state:
                fid = folders.create_folder(message.chat.id, data["folder_name"], folders.clean_emoji(text))
                if data.get("moving") or data.get("link"):
                    await finish_selection(message, state, fid, tracker, edit=False)
                else:
                    await reset_input(state)
                    await show_folders(message, state, edit=False)
            elif current in {Input.rename.state, Input.emoji.state}:
                fields = {"name": text} if current == Input.rename.state else {"emoji": text}
                folders.edit_folder(message.chat.id, data["edit_folder"], **fields)
                await reset_input(state)
                await show_list(message, state, data["edit_folder"], 0, edit=False)
            elif current in {Input.target.state, Input.percentage.state}:
                if current == Input.target.state:
                    products.set_alerts(
                        message.chat.id,
                        data["sid"],
                        mode="target",
                        target=products.parse_amount(text),
                        percent=0,
                    )
                else:
                    products.set_alerts(message.chat.id, data["sid"], percent=int(text))
                await reset_input(state)
                await show_product(message, state, data["sid"], "Alert settings saved.", edit=False)
            elif current == Input.search.state:
                if len(text) > 100:
                    raise ValueError("Use a search of at most 100 characters.")
                await reset_input(state)
                await state.update_data(query=text, view_page=0)
                await show_list(message, state, edit=False)
            else:
                await message.answer(
                    "Use Add product to track a URL, or choose another action.", reply_markup=menu()
                )
        except (ValueError, InvalidProduct) as exc:
            await message.answer(ESC(str(exc)), reply_markup=keyboard([("Cancel", "home")]))
        except sqlite3.IntegrityError:
            await state.set_state(Input.folder_name if current == Input.folder_emoji.state else current)
            await message.answer("That folder name already exists. Send a different name, or /cancel.")
        except Exception:
            log.exception("Input handler failed")
            await message.answer("That action failed. Try again or use /cancel.")

    return dp


async def main():
    settings = load_settings()
    backup = db.init_db(settings.database_path)
    if backup:
        log.info("Legacy database backed up before migration: %s", backup)
    async with create_session() as session:
        async with Bot(
            settings.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True)
        ) as bot:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Open the price tracker"),
                    BotCommand(command="cancel", description="Cancel the current operation"),
                    BotCommand(
                        command="quiet", description="Set quiet hours, e.g. /quiet 22 8 Europe/London"
                    ),
                ]
            )
            tracker = Tracker(session, bot, settings.price_refresh_minutes, settings.primary_chat_id)
            dispatcher = make_dispatcher(settings, tracker)
            workers = [asyncio.create_task(tracker.run_checks()), asyncio.create_task(tracker.run_delivery())]
            try:
                await dispatcher.start_polling(bot, close_bot_session=False)
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                await dispatcher.storage.close()
                await dispatcher.fsm.events_isolation.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
