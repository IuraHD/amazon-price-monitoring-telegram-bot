import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "tracker.db"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
 id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, name TEXT NOT NULL,
 emoji TEXT NOT NULL DEFAULT '📁', UNIQUE(chat_id, name)
);
CREATE TABLE IF NOT EXISTS catalog (
 id INTEGER PRIMARY KEY, asin TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
 title TEXT, price_pence INTEGER CHECK(price_pence > 0), currency TEXT NOT NULL DEFAULT 'GBP',
 status TEXT NOT NULL DEFAULT 'pending', detail TEXT,
 last_attempt TEXT, last_success TEXT, next_check TEXT, claim_until TEXT,
 seller TEXT, availability TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
 chat_id INTEGER NOT NULL, folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
 alerts_enabled INTEGER NOT NULL DEFAULT 1, alert_mode TEXT NOT NULL DEFAULT 'any',
 target_pence INTEGER, percent INTEGER NOT NULL DEFAULT 0, stock_alert INTEGER NOT NULL DEFAULT 0,
 created_ts TEXT NOT NULL, UNIQUE(chat_id, product_id)
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
 ts TEXT NOT NULL, price_pence INTEGER NOT NULL CHECK(price_pence > 0),
 source TEXT NOT NULL DEFAULT 'offer'
);
CREATE INDEX IF NOT EXISTS observations_product_ts ON observations(product_id, ts);
CREATE INDEX IF NOT EXISTS subscriptions_chat_folder ON subscriptions(chat_id, folder_id);
CREATE TABLE IF NOT EXISTS preferences (
 chat_id INTEGER PRIMARY KEY, quiet_start INTEGER, quiet_end INTEGER,
 timezone TEXT NOT NULL DEFAULT 'Europe/London', blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 observation_id INTEGER REFERENCES observations(id) ON DELETE CASCADE,
 text TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt TEXT NOT NULL, claim_until TEXT, sent_ts TEXT,
 UNIQUE(subscription_id, observation_id)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pence(value) -> int | None:
    try:
        result = int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return result if result > 0 else None
    except (ValueError, TypeError, ArithmeticError):
        return None


@contextmanager
def connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _legacy_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return utcnow()


def init_db(path=None):
    global DB_PATH
    if path is not None:
        DB_PATH = Path(path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database was created by a newer application version")
        if version == SCHEMA_VERSION:
            return None
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        backup = None
        if "products" in tables:
            backup = DB_PATH.with_name(
                DB_PATH.name + ".pre-v1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".bak"
            )
            dest = sqlite3.connect(backup)
            try:
                conn.backup(dest)
            finally:
                dest.close()
        # Every schema/data change is one transaction; the backup is made beforehand.
        conn.execute("BEGIN IMMEDIATE")
        if "products" in tables:
            for table in ("folders", "products", "price_history"):
                if table in tables:
                    conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        if "products" in tables:
            if "folders" in tables:
                conn.execute(
                    "INSERT INTO folders(id,chat_id,name,emoji) SELECT id,chat_id,name,COALESCE(emoji,'📁') FROM legacy_folders"
                )
            for row in conn.execute("SELECT * FROM legacy_products").fetchall():
                asin = row["asin"] or f"legacy-{row['id']}"
                conn.execute(
                    "INSERT OR IGNORE INTO catalog(asin,url,title,price_pence,status,detail) VALUES(?,?,?,?,?,?)",
                    (
                        asin,
                        row["url"],
                        row["title"] if "title" in row.keys() else None,
                        pence(row["last_price"]),
                        "unverified",
                        "Imported legacy price; awaiting a verified check",
                    ),
                )
                pid = conn.execute("SELECT id FROM catalog WHERE asin=?", (asin,)).fetchone()[0]
                folder = (
                    conn.execute(
                        "SELECT id FROM folders WHERE id=? AND chat_id=?", (row["folder_id"], row["chat_id"])
                    ).fetchone()
                    if "folder_id" in row.keys()
                    else None
                )
                conn.execute(
                    "INSERT INTO subscriptions(id,product_id,chat_id,folder_id,alerts_enabled,created_ts) VALUES(?,?,?,?,?,?)",
                    (
                        row["id"],
                        pid,
                        row["chat_id"],
                        folder[0] if folder else None,
                        row["alerts_enabled"],
                        _legacy_time(row["created_ts"]),
                    ),
                )
                if "price_history" in tables:
                    for h in conn.execute(
                        "SELECT ts,price FROM legacy_price_history WHERE product_id=?", (row["id"],)
                    ).fetchall():
                        amount = pence(h["price"])
                        if amount:
                            conn.execute(
                                "INSERT INTO observations(product_id,ts,price_pence,source) VALUES(?,?,?,'legacy')",
                                (pid, _legacy_time(h["ts"]), amount),
                            )
            for table in ("price_history", "products", "folders"):
                if table in tables:
                    conn.execute(f"DROP TABLE legacy_{table}")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database migration failed its foreign-key check")
        return backup


def fetch_one(query, params=()):
    with connection() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def fetch_all(query, params=()):
    with connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def execute(query, params=()):
    with connection() as conn:
        return conn.execute(query, params).lastrowid
