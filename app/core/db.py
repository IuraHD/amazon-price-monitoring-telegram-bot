import sqlite3
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).parent.parent / "data" / "tracker.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '📁',
    chat_id INTEGER NOT NULL,
    created_ts TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, chat_id)
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asin TEXT UNIQUE,
    url TEXT NOT NULL,
    alerts_enabled INTEGER NOT NULL DEFAULT 1,
    last_price REAL,
    last_graph_message_id INTEGER,
    chat_id INTEGER NOT NULL,
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    created_ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    price REAL NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE products ADD COLUMN title TEXT")
        except Exception:
            pass


def fetch_one(query: str, params: Iterable[Any] = ()):
    with get_conn() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: Iterable[Any] = ()):
    with get_conn() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


def execute(query: str, params: Iterable[Any] = ()) -> int:
    with get_conn() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.lastrowid
