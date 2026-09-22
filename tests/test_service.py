import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.core import db, products
from app.core.service import Tracker
from app.utils.fetch import Offer, ProductLink


def tracked(chat=111, asin="B012345678"):
    sid, _ = products.add_product(chat, ProductLink(asin, f"https://www.amazon.co.uk/dp/{asin}"))
    return sid, products.get_product(chat, sid)["product_id"]


def due():
    with db.connection() as c:
        c.execute("UPDATE catalog SET last_attempt=NULL,next_check=NULL,claim_until=NULL")


def queue(chat=111, asin="B012345678"):
    sid, pid = tracked(chat, asin)
    products.record_offer(pid, Offer("ok", 1000, "Product"), 10)
    products.record_offer(pid, Offer("ok", 900, "Product"), 10)
    return sid, pid


async def test_overlapping_manual_checks_fetch_once():
    sid, pid = tracked()

    async def fetch(*args):
        await asyncio.sleep(0.02)
        return Offer("ok", 1000)

    fetcher = AsyncMock(side_effect=fetch)
    tracker = Tracker(None, SimpleNamespace(send_message=AsyncMock()), fetcher=fetcher)
    results = await asyncio.gather(tracker.refresh(pid, manual=True), tracker.refresh(pid, manual=True))
    assert sorted(results) == ["changed", "skipped"]
    assert fetcher.await_count == 1
    assert await tracker.refresh(pid, manual=True) == "skipped"


async def test_manual_refresh_chat_is_scoped_and_summary_honest():
    tracked(111)
    tracked(222, "B012345679")
    fetcher = AsyncMock(return_value=Offer("blocked"))
    tracker = Tracker(None, None, fetcher=fetcher)
    summary = await tracker.refresh_chat(111)
    assert summary == {"changed": 0, "unchanged": 0, "failed": 1, "skipped": 0}
    assert fetcher.await_count == 1
    assert db.fetch_one("SELECT last_attempt FROM catalog WHERE asin='B012345679'")["last_attempt"] is None


async def test_failure_isolated_to_one_product():
    tracked(111)
    tracked(222, "B012345679")

    async def fetch(asin, session):
        if asin == "B012345678":
            raise RuntimeError("fixture failure")
        return Offer("ok", 1200)

    tracker = Tracker(None, None, fetcher=fetch)
    assert sorted(await tracker.refresh_due()) == ["changed", "failed"]


async def test_muted_product_has_no_delivery_or_chart():
    sid, pid = tracked()
    products.set_alerts(111, sid, enabled=False)
    products.record_offer(pid, Offer("ok", 1000), 10)
    products.record_offer(pid, Offer("ok", 900), 10)
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    await Tracker(None, bot).deliver()
    bot.send_message.assert_not_awaited()
    bot.send_photo.assert_not_awaited()


async def test_failed_delivery_retried_without_new_price_change():
    queue()
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[OSError("offline"), None]))
    tracker = Tracker(None, bot)
    await tracker.deliver()
    row = db.fetch_one("SELECT * FROM outbox")
    assert row["state"] == "pending" and row["attempts"] == 1
    with db.connection() as c:
        c.execute("UPDATE outbox SET next_attempt='2000-01-01'")
    await tracker.deliver()
    assert db.fetch_one("SELECT * FROM outbox")["state"] == "sent"
    assert bot.send_message.await_count == 2


async def test_delivery_failure_does_not_stop_other_chat():
    queue(111)
    queue(222, "B012345679")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[OSError("offline"), None]))
    await Tracker(None, bot).deliver()
    assert bot.send_message.await_count == 2
    assert len(db.fetch_all("SELECT * FROM outbox WHERE state='sent'")) == 1


async def test_blocked_chat_paused():
    queue()
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramForbiddenError(method=SendMessage(chat_id=111, text="x"), message="blocked")
        )
    )
    tracker = Tracker(None, bot)
    await tracker.deliver()
    await tracker.deliver()
    assert bot.send_message.await_count == 1
    assert db.fetch_one("SELECT * FROM preferences WHERE chat_id=111")["blocked"] == 1


async def test_quiet_hours_defer_without_losing_notification():
    queue()
    hour = datetime.now(timezone.utc).hour
    products.set_quiet_hours(111, hour, (hour + 1) % 24, "UTC")
    bot = SimpleNamespace(send_message=AsyncMock())
    tracker = Tracker(None, bot)
    await tracker.deliver()
    bot.send_message.assert_not_awaited()
    assert db.fetch_one("SELECT * FROM outbox")["state"] == "pending"
    products.set_quiet_hours(111)
    with db.connection() as conn:
        conn.execute("UPDATE outbox SET next_attempt='2000-01-01'")
    await tracker.deliver()
    bot.send_message.assert_awaited_once()


async def test_retry_after_is_respected():
    queue()
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramRetryAfter(
                method=SendMessage(chat_id=111, text="x"), message="limited", retry_after=120
            )
        )
    )
    await Tracker(None, bot).deliver()
    row = db.fetch_one("SELECT * FROM outbox")
    assert datetime.fromisoformat(row["next_attempt"]) >= datetime.now(timezone.utc) + timedelta(seconds=115)


async def test_delivery_lease_prevents_concurrent_duplicates():
    queue()

    async def send(*args):
        await asyncio.sleep(0.02)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
    tracker = Tracker(None, bot)
    await asyncio.gather(tracker.deliver(), tracker.deliver())
    assert bot.send_message.await_count == 1


async def test_restricted_scheduler_and_sender():
    queue(111)
    queue(222, "B012345679")
    due()
    bot = SimpleNamespace(send_message=AsyncMock())
    fetcher = AsyncMock(return_value=Offer("ok", 900))
    tracker = Tracker(None, bot, primary_chat_id=111, fetcher=fetcher)
    await tracker.refresh_due()
    await tracker.deliver()
    assert fetcher.await_count == 1
    assert all(call.args[0] == 111 for call in bot.send_message.call_args_list)
    with pytest.raises(ValueError):
        await tracker.refresh_chat(222)


async def test_deleted_product_during_fetch_is_safe():
    sid, pid = tracked()

    async def fetch(*args):
        products.remove_product(111, sid)
        return Offer("ok", 1000)

    assert await Tracker(None, None, fetcher=fetch).refresh(pid, manual=True) == "skipped"
