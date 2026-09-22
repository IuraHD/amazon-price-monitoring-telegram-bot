import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from .db import connection, fetch_all, fetch_one, utcnow
from .products import is_quiet, record_offer
from app.utils.fetch import Offer, fetch_offer

log = logging.getLogger(__name__)


class Tracker:
    def __init__(self, session, bot, interval=10, primary_chat_id=None, fetcher=fetch_offer):
        self.session = session
        self.bot = bot
        self.interval = interval
        self.primary_chat_id = primary_chat_id
        self.fetcher = fetcher
        self.slots = asyncio.Semaphore(3)

    async def refresh(self, pid, *, manual=False):
        async with self.slots:
            now = datetime.now(timezone.utc)
            stamp = now.isoformat(timespec="seconds")
            cutoff = (now - timedelta(seconds=60)).isoformat(timespec="seconds")
            lease = (now + timedelta(minutes=3)).isoformat(timespec="seconds")
            with connection() as conn:
                row = conn.execute("SELECT * FROM catalog WHERE id=?", (pid,)).fetchone()
                if not row:
                    return "skipped"
                cursor = conn.execute(
                    """UPDATE catalog SET claim_until=?,last_attempt=? WHERE id=?
                    AND (claim_until IS NULL OR claim_until<=?)
                    AND (last_attempt IS NULL OR last_attempt<=?)
                    AND (? OR next_check IS NULL OR next_check<=?)""",
                    (lease, stamp, pid, stamp, cutoff, int(manual), stamp),
                )
                if not cursor.rowcount:
                    return "skipped"
            try:
                offer = await self.fetcher(row["asin"], self.session)
                return record_offer(pid, offer, self.interval)
            except asyncio.CancelledError:
                with connection() as conn:
                    conn.execute("UPDATE catalog SET claim_until=NULL WHERE id=?", (pid,))
                raise
            except Exception:
                log.exception("Product refresh failed: product_id=%s", pid)
                record_offer(
                    pid, Offer("internal_error", detail="Check failed; retry scheduled"), self.interval
                )
                return "failed"

    async def refresh_chat(self, chat_id):
        if self.primary_chat_id is not None and chat_id != self.primary_chat_id:
            raise ValueError("This chat is not authorized.")
        rows = fetch_all("SELECT DISTINCT product_id FROM subscriptions WHERE chat_id=?", (chat_id,))
        counts = Counter(await asyncio.gather(*(self.refresh(r["product_id"], manual=True) for r in rows)))
        return {name: counts[name] for name in ("changed", "unchanged", "failed", "skipped")}

    async def refresh_due(self):
        sql = """SELECT DISTINCT c.id FROM catalog c JOIN subscriptions s ON s.product_id=c.id
                 LEFT JOIN preferences p ON p.chat_id=s.chat_id
                 WHERE (c.next_check IS NULL OR c.next_check<=?) AND COALESCE(p.blocked,0)=0"""
        args = [utcnow()]
        if self.primary_chat_id is not None:
            sql += " AND s.chat_id=?"
            args.append(self.primary_chat_id)
        rows = fetch_all(sql + " ORDER BY c.next_check,c.id LIMIT 100", args)
        return await asyncio.gather(*(self.refresh(r["id"]) for r in rows))

    async def deliver(self):
        now = utcnow()
        sql = """SELECT o.*,s.chat_id FROM outbox o JOIN subscriptions s ON s.id=o.subscription_id
                 LEFT JOIN preferences p ON p.chat_id=s.chat_id
                 WHERE s.alerts_enabled=1 AND COALESCE(p.blocked,0)=0 AND
                 ((o.state='pending' AND o.next_attempt<=?) OR (o.state='sending' AND o.claim_until<=?))"""
        args = [now, now]
        if self.primary_chat_id is not None:
            sql += " AND s.chat_id=?"
            args.append(self.primary_chat_id)
        rows = fetch_all(sql + " ORDER BY o.id LIMIT 100", args)
        for row in rows:
            pref = fetch_one("SELECT * FROM preferences WHERE chat_id=?", (row["chat_id"],))
            if pref and pref["blocked"]:
                continue
            if is_quiet(pref):
                retry = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")
                with connection() as conn:
                    conn.execute(
                        "UPDATE outbox SET state='pending',next_attempt=?,claim_until=NULL WHERE id=? AND (state='pending' OR claim_until<=?)",
                        (retry, row["id"], now),
                    )
                continue
            lease = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec="seconds")
            with connection() as conn:
                claimed = conn.execute(
                    """UPDATE outbox SET state='sending',claim_until=?
                    WHERE id=? AND ((state='pending' AND next_attempt<=?) OR (state='sending' AND claim_until<=?))""",
                    (lease, row["id"], now, now),
                ).rowcount
            if not claimed:
                continue
            try:
                await self.bot.send_message(row["chat_id"], row["text"])
            except TelegramForbiddenError:
                with connection() as conn:
                    conn.execute(
                        "INSERT INTO preferences(chat_id,blocked) VALUES(?,1) ON CONFLICT(chat_id) DO UPDATE SET blocked=1",
                        (row["chat_id"],),
                    )
                    conn.execute(
                        "UPDATE outbox SET state='pending',claim_until=NULL WHERE id=?", (row["id"],)
                    )
                log.info("Paused notifications for unreachable chat")
            except TelegramBadRequest:
                with connection() as conn:
                    conn.execute("UPDATE outbox SET state='failed',claim_until=NULL WHERE id=?", (row["id"],))
                log.exception("Telegram rejected notification: outbox_id=%s", row["id"])
            except Exception as exc:
                delay = (
                    exc.retry_after
                    if isinstance(exc, TelegramRetryAfter)
                    else min(3600, 30 * 2 ** min(row["attempts"], 7))
                )
                retry = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
                with connection() as conn:
                    conn.execute(
                        "UPDATE outbox SET state='pending',attempts=attempts+1,next_attempt=?,claim_until=NULL WHERE id=?",
                        (retry, row["id"]),
                    )
                log.warning(
                    "Notification retry scheduled: outbox_id=%s error=%s", row["id"], type(exc).__name__
                )
            else:
                with connection() as conn:
                    conn.execute(
                        "UPDATE outbox SET state='sent',sent_ts=?,claim_until=NULL WHERE id=?",
                        (utcnow(), row["id"]),
                    )

    async def run_checks(self):
        while True:
            try:
                await self.refresh_due()
            except Exception:
                log.exception("Scheduled refresh failed")
            await asyncio.sleep(15)

    async def run_delivery(self):
        while True:
            try:
                await self.deliver()
            except Exception:
                log.exception("Notification delivery pass failed")
            await asyncio.sleep(10)
