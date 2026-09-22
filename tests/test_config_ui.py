from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Chat, User, Update, CallbackQuery, MessageEntity
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from app.bot.bot import make_dispatcher, product_text, operation, reset_input
from app.bot.states import Input
from app.core.config import Settings, load_settings
from app.core import products, folders
from app.utils.fetch import ProductLink


@pytest.mark.parametrize("chat", ["-1001234567890", "123"])
def test_config_signed_chat_ids(monkeypatch, chat):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_PRIMARY_CHAT_ID", chat)
    assert load_settings().primary_chat_id == int(chat)


@pytest.mark.parametrize("chat", ["0", "not-an-id"])
def test_bad_restriction_fails_closed(monkeypatch, chat):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_PRIMARY_CHAT_ID", chat)
    with pytest.raises(ValueError):
        load_settings()


def test_html_text_is_escaped():
    fid = folders.create_folder(111, "<b>Bad & name</b>")
    sid, _ = products.add_product(
        111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"), fid
    )
    text = product_text(products.get_product(111, sid))
    assert "&lt;b&gt;Bad &amp; name&lt;/b&gt;" in text


def test_old_operation_token_is_rejected():
    with pytest.raises(ValueError):
        operation({"op": "new"}, "old")


async def test_reset_discards_stale_move_but_keeps_navigation():
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=111, user_id=111))
    await state.set_state(Input.url)
    await state.set_data({"moving": 77, "link": {}, "view_folder": "4", "view_page": 2})
    await reset_input(state)
    assert await state.get_state() is None
    assert await state.get_data() == {"view_folder": "4", "view_page": 2}


class Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.counter = 0

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if type(method).__name__ in {"AnswerCallbackQuery", "DeleteMessage"}:
            return True
        self.counter += 1
        return Message(
            message_id=self.counter,
            date=0,
            chat=Chat(id=method.chat_id, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
            text=getattr(method, "text", None),
            reply_markup=getattr(method, "reply_markup", None),
        )


@pytest.fixture
async def ui():
    session = Session()
    bot = Bot("123456:TESTTOKEN", session=session)
    tracker = SimpleNamespace(
        session=None,
        refresh=AsyncMock(return_value="failed"),
        refresh_chat=AsyncMock(return_value={"changed": 0, "unchanged": 0, "failed": 0, "skipped": 0}),
    )
    dp = make_dispatcher(Settings("123456:TESTTOKEN"), tracker)
    yield bot, dp, session, tracker
    await dp.storage.close()
    await dp.fsm.events_isolation.close()
    await bot.session.close()


async def send(ui, text=None, callback=None, chat=111):
    bot, dp, session, _ = ui
    user = User(id=chat, is_bot=False, first_name="User")
    message = Message(
        message_id=100,
        date=0,
        chat=Chat(id=chat, type="private"),
        from_user=user,
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
        if text and text.startswith("/")
        else None,
    )
    update = (
        Update(
            update_id=1,
            callback_query=CallbackQuery(
                id="test", from_user=user, chat_instance="x", message=message, data=callback
            ),
        )
        if callback
        else Update(update_id=1, message=message)
    )
    await dp.feed_update(bot, update)
    return session.calls[-1]


async def test_add_create_folder_resumes_and_duplicate_explained(ui):
    await send(ui, "/start")
    await send(ui, callback="add")
    last = await send(ui, "https://www.amazon.co.uk/dp/B012345678")
    new = next(
        b.callback_data for row in last.reply_markup.inline_keyboard for b in row if b.text == "Create folder"
    )
    await send(ui, callback=new)
    await send(ui, "Hardware")
    last = await send(ui, "👩🏽‍💻")
    assert "Tracking added" in last.text
    rows, _, _ = products.list_products(111)
    assert rows[0]["folder_name"] == "Hardware"
    await send(ui, callback="add")
    last = await send(ui, "https://www.amazon.co.uk/dp/B012345678")
    nofolder = next(
        b.callback_data for row in last.reply_markup.inline_keyboard for b in row if b.text == "No folder"
    )
    last = await send(ui, callback=nofolder)
    assert "Already tracked" in last.text
    assert products.get_product(111, rows[0]["id"])["folder_name"] == "Hardware"


async def test_rename_emoji_and_delete_confirmation(ui):
    fid = folders.create_folder(111, "Old")
    sid, _ = products.add_product(
        111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"), fid
    )
    last = await send(ui, callback=f"list:{fid}:0")
    assert any(b.callback_data == f"rename:{fid}" for row in last.reply_markup.inline_keyboard for b in row)
    await send(ui, callback=f"rename:{fid}")
    await send(ui, "New")
    await send(ui, callback=f"emoji:{fid}")
    await send(ui, "🇬🇧")
    assert folders.get_folder(111, fid)["name"] == "New"
    last = await send(ui, callback=f"delfolder:{fid}")
    assert folders.get_folder(111, fid)
    confirm = last.reply_markup.inline_keyboard[0][0].callback_data
    await send(ui, callback=confirm)
    assert products.get_product(111, sid)["folder_id"] is None


async def test_nontext_and_cancel_input(ui):
    await send(ui, callback="add")
    last = await send(ui, text=None)
    assert "Please send text" in last.text
    await send(ui, "/cancel")
    last = await send(ui, "random")
    assert "Use Add product" in last.text


async def test_foreign_callback_cannot_remove(ui):
    sid, _ = products.add_product(111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"))
    last = await send(ui, callback=f"remove:{sid}", chat=222)
    assert "no longer exists in your chat" in last.text
    assert products.get_product(111, sid)


async def test_marketplace_confirmation(ui):
    await send(ui, callback="add")
    last = await send(ui, "https://www.amazon.de/dp/B012345678")
    assert "another marketplace" in last.text
    assert products.list_products(111)[1] == 0
    await send(ui, callback=last.reply_markup.inline_keyboard[0][0].callback_data)


async def test_target_and_percentage_flow(ui):
    sid, _ = products.add_product(111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"))
    await send(ui, callback=f"target:{sid}")
    await send(ui, "10.01")
    row = products.get_product(111, sid)
    assert row["target_pence"] == 1001 and row["alert_mode"] == "target"
    await send(ui, callback=f"percent:{sid}")
    await send(ui, "5")
    assert products.get_product(111, sid)["percent"] == 5


async def test_restriction_applies_to_callbacks_and_input(database):
    session = Session()
    bot = Bot("123456:TESTTOKEN", session=session)
    tracker = SimpleNamespace(session=None, refresh=AsyncMock(), refresh_chat=AsyncMock())
    dp = make_dispatcher(Settings("123456:TESTTOKEN", primary_chat_id=111), tracker)
    context = (bot, dp, session, tracker)
    try:
        last = await send(context, "/start", chat=222)
        assert "available in private chats" in last.text
        last = await send(context, callback="refresh_all", chat=222)
        assert last.text == "This chat or user is not authorized."
        tracker.refresh_chat.assert_not_awaited()
        last = await send(context, "https://amazon.co.uk/dp/B012345678", chat=222)
        assert "available in private chats" in last.text
    finally:
        await dp.storage.close()
        await dp.fsm.events_isolation.close()
        await bot.session.close()


async def test_move_create_folder_resumes(ui):
    sid, _ = products.add_product(111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"))
    last = await send(ui, callback=f"move:{sid}")
    new = next(
        b.callback_data for row in last.reply_markup.inline_keyboard for b in row if b.text == "Create folder"
    )
    await send(ui, callback=new)
    await send(ui, "Moved")
    last = await send(ui, "/skip")
    assert "Product moved" in last.text
    assert products.get_product(111, sid)["folder_name"] == "Moved"


async def test_old_confirm_cannot_repeat_or_delete_other_product(ui):
    sid, _ = products.add_product(111, ProductLink("B012345678", "https://www.amazon.co.uk/dp/B012345678"))
    last = await send(ui, callback=f"remove:{sid}")
    token = last.reply_markup.inline_keyboard[0][0].callback_data
    await send(ui, callback="home")
    last = await send(ui, callback=token)
    assert "expired" in last.text
    assert products.get_product(111, sid)
