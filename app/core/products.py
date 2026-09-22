import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import connection, fetch_all, fetch_one, utcnow

JOIN = """SELECT s.*,c.asin,c.url,c.title,c.price_pence,c.currency,c.status,c.detail,
 c.last_attempt,c.last_success,c.availability,c.seller,f.name AS folder_name,f.emoji AS folder_emoji
 FROM subscriptions s JOIN catalog c ON c.id=s.product_id
 LEFT JOIN folders f ON f.id=s.folder_id AND f.chat_id=s.chat_id"""


def money(value):
    return f"£{value / 100:,.2f}" if value is not None else "unknown"


def parse_amount(value):
    try:
        value = Decimal(value.strip().replace("£", "").replace(",", ""))
        if (
            not value.is_finite()
            or value <= 0
            or value > 1_000_000
            or value * 100 != (value * 100).to_integral_value()
        ):
            raise ValueError
        return int(value * 100)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            "Enter a positive GBP amount with at most two decimal places, such as 29.99."
        ) from exc


def get_product(chat_id, subscription_id):
    return fetch_one(JOIN + " WHERE s.chat_id=? AND s.id=?", (chat_id, subscription_id))


def add_product(chat_id, link, folder_id=None):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if (
            folder_id
            and not conn.execute(
                "SELECT 1 FROM folders WHERE id=? AND chat_id=?", (folder_id, chat_id)
            ).fetchone()
        ):
            raise ValueError("The selected folder no longer exists.")
        existing = conn.execute(
            "SELECT s.id FROM subscriptions s JOIN catalog c ON c.id=s.product_id WHERE c.asin=? AND s.chat_id=?",
            (link.asin, chat_id),
        ).fetchone()
        if existing:
            return existing[0], False
        if (
            conn.execute("SELECT COUNT(*) FROM subscriptions WHERE chat_id=?", (chat_id,)).fetchone()[0]
            >= 200
        ):
            raise ValueError("Limit reached: 200 tracked products per chat.")
        conn.execute("INSERT OR IGNORE INTO catalog(asin,url) VALUES(?,?)", (link.asin, link.url))
        pid = conn.execute("SELECT id FROM catalog WHERE asin=?", (link.asin,)).fetchone()[0]
        sid = conn.execute(
            "INSERT INTO subscriptions(product_id,chat_id,folder_id,created_ts) VALUES(?,?,?,?)",
            (pid, chat_id, folder_id, utcnow()),
        ).lastrowid
        conn.execute("INSERT OR IGNORE INTO preferences(chat_id) VALUES(?)", (chat_id,))
        return sid, True


def list_products(chat_id, folder="all", query="", sort="recent", page=0, page_size=8):
    where = " WHERE s.chat_id=?"
    args = [chat_id]
    if folder != "all":
        if int(folder) == 0:
            where += " AND s.folder_id IS NULL"
        else:
            where += " AND s.folder_id=?"
            args.append(int(folder))
    if query:
        where += " AND (instr(lower(COALESCE(c.title,'')),lower(?))>0 OR instr(lower(c.asin),lower(?))>0)"
        args.extend([query, query])
    order = {
        "recent": "s.id DESC",
        "name": "COALESCE(c.title,c.asin) COLLATE NOCASE",
        "price": "c.price_pence IS NULL,c.price_pence,s.id",
    }.get(sort, "s.id DESC")
    with connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM (" + JOIN + where + ")", args).fetchone()[0]
        page = max(0, min(page, (count - 1) // page_size if count else 0))
        rows = conn.execute(
            JOIN + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", [*args, page_size, page * page_size]
        ).fetchall()
    return [dict(r) for r in rows], count, page


def move_product(chat_id, sid, folder_id):
    with connection() as conn:
        if (
            folder_id
            and not conn.execute(
                "SELECT 1 FROM folders WHERE id=? AND chat_id=?", (folder_id, chat_id)
            ).fetchone()
        ):
            raise ValueError("Folder no longer exists.")
        if not conn.execute(
            "UPDATE subscriptions SET folder_id=? WHERE id=? AND chat_id=?", (folder_id, sid, chat_id)
        ).rowcount:
            raise ValueError("Product no longer exists.")


def list_summary(chat_id, folder="all", query=""):
    condition = " WHERE s.chat_id=?"
    args = [chat_id]
    if folder != "all":
        condition += " AND s.folder_id IS NULL" if int(folder) == 0 else " AND s.folder_id=?"
        if int(folder):
            args.append(int(folder))
    if query:
        condition += " AND (instr(lower(COALESCE(c.title,'')),lower(?))>0 OR instr(lower(c.asin),lower(?))>0)"
        args.extend([query, query])
    return fetch_one(
        """SELECT COALESCE(SUM(c.price_pence),0) AS total_pence,
        COALESCE(SUM(c.price_pence IS NULL),0) AS unknown, COALESCE(SUM(c.status!='ok'),0) AS needs_check
        FROM subscriptions s JOIN catalog c ON c.id=s.product_id"""
        + condition,
        args,
    )


def remove_product(chat_id, sid):
    with connection() as conn:
        row = conn.execute(
            "SELECT product_id FROM subscriptions WHERE id=? AND chat_id=?", (sid, chat_id)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM subscriptions WHERE id=? AND chat_id=?", (sid, chat_id))
            conn.execute(
                "DELETE FROM catalog WHERE id=? AND NOT EXISTS (SELECT 1 FROM subscriptions WHERE product_id=?)",
                (row[0], row[0]),
            )


def set_alerts(chat_id, sid, *, enabled=None, mode=None, target=None, percent=None, stock=None):
    with connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM subscriptions WHERE id=? AND chat_id=?", (sid, chat_id)
        ).fetchone():
            raise ValueError("Product no longer exists.")
        changes = {}
        if enabled is not None:
            changes["alerts_enabled"] = int(bool(enabled))
        if mode is not None:
            if mode not in {"any", "drop", "target"}:
                raise ValueError("Unknown alert mode")
            changes["alert_mode"] = mode
        if target is not None:
            if target <= 0:
                raise ValueError("Target must be positive")
            changes["target_pence"] = target
        if percent is not None:
            if not 0 <= percent <= 100:
                raise ValueError("Percentage must be between 0 and 100")
            changes["percent"] = percent
        if stock is not None:
            changes["stock_alert"] = int(bool(stock))
        if changes:
            conn.execute(
                "UPDATE subscriptions SET "
                + ",".join(k + "=?" for k in changes)
                + " WHERE id=? AND chat_id=?",
                [*changes.values(), sid, chat_id],
            )
            conn.execute("DELETE FROM outbox WHERE subscription_id=? AND state!='sent'", (sid,))


def record_offer(pid, offer, interval_minutes):
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    next_check = (
        now + timedelta(minutes=interval_minutes if offer.status == "ok" else max(15, interval_minutes))
    ).isoformat(timespec="seconds")
    with connection() as conn:
        old = conn.execute("SELECT * FROM catalog WHERE id=?", (pid,)).fetchone()
        if not old:
            return "skipped"
        conn.execute(
            """UPDATE catalog SET title=COALESCE(?,title),status=?,detail=?,last_attempt=?,
            next_check=?,claim_until=NULL,availability=?,seller=? WHERE id=?""",
            (
                offer.title,
                offer.status,
                offer.detail,
                stamp,
                next_check,
                offer.availability if offer.availability != "unknown" else old["availability"],
                offer.seller,
                pid,
            ),
        )
        if offer.status != "ok" or offer.price_pence is None:
            return "failed"
        conn.execute(
            "UPDATE catalog SET price_pence=?,last_success=? WHERE id=?", (offer.price_pence, stamp, pid)
        )
        oid = conn.execute(
            "INSERT INTO observations(product_id,ts,price_pence) VALUES(?,?,?)",
            (pid, stamp, offer.price_pence),
        ).lastrowid
        previous = old["price_pence"] if old["last_success"] else None
        changed = previous is None or previous != offer.price_pence
        for sub in conn.execute(
            "SELECT * FROM subscriptions WHERE product_id=? AND alerts_enabled=1", (pid,)
        ).fetchall():
            price_alert = previous is not None and previous != offer.price_pence
            if sub["alert_mode"] == "drop":
                price_alert = price_alert and offer.price_pence < previous
            elif sub["alert_mode"] == "target":
                price_alert = (
                    price_alert
                    and sub["target_pence"] is not None
                    and previous > sub["target_pence"] >= offer.price_pence
                )
            if price_alert and sub["percent"]:
                price_alert = abs(offer.price_pence - previous) * 100 >= previous * sub["percent"]
            stock_alert = (
                sub["stock_alert"]
                and old["availability"] == "unavailable"
                and offer.availability == "available"
            )
            if price_alert or stock_alert:
                title = escape(offer.title or old["title"] or old["asin"])
                text = f"<b>{title}</b>\n"
                if price_alert:
                    text += f"{money(previous)} → {money(offer.price_pence)}\n"
                if stock_alert:
                    text += "Amazon reports this product back in stock.\n"
                text += f'<a href="https://www.amazon.co.uk/dp/{escape(old["asin"], quote=True)}">Open on Amazon UK</a>\nShipping and conditional discounts excluded.'
                conn.execute(
                    "INSERT OR IGNORE INTO outbox(subscription_id,observation_id,text,next_attempt) VALUES(?,?,?,?)",
                    (sub["id"], oid, text, stamp),
                )
        return "changed" if changed else "unchanged"


def history(chat_id, sid, days=30, limit=None):
    if days not in {7, 30, 90}:
        raise ValueError("Choose 7, 30, or 90 days")
    row = get_product(chat_id, sid)
    if not row:
        raise ValueError("Product no longer exists.")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = fetch_all(
        "SELECT ts,price_pence,source FROM observations WHERE product_id=? AND ts>=? ORDER BY ts,id",
        (row["product_id"], since),
    )
    if limit and len(rows) > limit:
        bucket_count = max(1, (limit - 2) // 2)
        chosen = {0, len(rows) - 1}
        for i in range(bucket_count):
            start = 1 + i * (len(rows) - 2) // bucket_count
            end = 1 + (i + 1) * (len(rows) - 2) // bucket_count
            if start < end:
                chosen.add(min(range(start, end), key=lambda k: rows[k]["price_pence"]))
                chosen.add(max(range(start, end), key=lambda k: rows[k]["price_pence"]))
        return [rows[i] for i in sorted(chosen)]
    return rows


def export_csv(chat_id, sid, days=90):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["timestamp_utc", "price_gbp", "source"])
    for row in history(chat_id, sid, days):
        writer.writerow([row["ts"], f"{row['price_pence'] / 100:.2f}", row["source"]])
    return output.getvalue().encode("utf-8")


def set_quiet_hours(chat_id, start=None, end=None, timezone_name="Europe/London"):
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown timezone; use a name such as Europe/London.") from exc
    if start is not None and (end is None or not 0 <= start < 24 or not 0 <= end < 24 or start == end):
        raise ValueError("Use different start/end hours between 0 and 23.")
    with connection() as conn:
        conn.execute(
            """INSERT INTO preferences(chat_id,quiet_start,quiet_end,timezone) VALUES(?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,timezone=excluded.timezone""",
            (chat_id, start, end, timezone_name),
        )


def is_quiet(pref, now=None):
    if not pref or pref["quiet_start"] is None:
        return False
    hour = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(pref["timezone"])).hour
    start, end = pref["quiet_start"], pref["quiet_end"]
    return start <= hour < end if start < end else hour >= start or hour < end
