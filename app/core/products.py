from typing import Optional, Sequence
import datetime as dt

from .db import fetch_one, fetch_all, execute

PRICE_SELECT_LIMIT = 200  # max points for graph simplification


def add_product(asin: str, url: str, chat_id: int, folder_id: Optional[int] = None) -> int:
    existing = fetch_one("SELECT id FROM products WHERE asin = ?", (asin,))
    if existing:
        return existing["id"]
    return execute("INSERT INTO products(asin,url,chat_id,folder_id) VALUES(?,?,?,?)", (asin, url, chat_id, folder_id))


def update_price(product_id: int, price: float) -> None:
    execute("UPDATE products SET last_price = ? WHERE id = ?", (price, product_id))
    execute(
        "INSERT INTO price_history(product_id, ts, price) VALUES(?,?,?)",
        (product_id, dt.datetime.now().isoformat(), price),
    )


def toggle_alerts(product_id: int, enabled: bool) -> None:
    execute("UPDATE products SET alerts_enabled = ? WHERE id = ?", (1 if enabled else 0, product_id))


def update_graph_message(product_id: int, message_id: int | None) -> None:
    execute("UPDATE products SET last_graph_message_id = ? WHERE id = ?", (message_id, product_id))


def remove_product(product_id: int) -> None:
    execute("DELETE FROM products WHERE id = ?", (product_id,))


def get_product(product_id: int):
    return fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))


def get_product_by_asin(asin: str):
    return fetch_one("SELECT * FROM products WHERE asin = ?", (asin,))


def update_title(product_id: int, title: str | None) -> None:
    execute("UPDATE products SET title = ? WHERE id = ?", (title, product_id))


def list_products(chat_id: int, folder_id: Optional[int] = None) -> Sequence:
    if folder_id is None:
        # Get all products with folder info
        return fetch_all("""
            SELECT p.*, f.name as folder_name, f.emoji as folder_emoji
            FROM products p
            LEFT JOIN folders f ON p.folder_id = f.id
            WHERE p.chat_id = ?
            ORDER BY p.created_ts DESC
        """, (chat_id,))
    else:
        return fetch_all("""
            SELECT p.*, f.name as folder_name, f.emoji as folder_emoji
            FROM products p
            LEFT JOIN folders f ON p.folder_id = f.id
            WHERE p.chat_id = ? AND p.folder_id = ?
            ORDER BY p.created_ts DESC
        """, (chat_id, folder_id))


def list_products_without_folder(chat_id: int) -> Sequence:
    """List products not assigned to any folder."""
    return fetch_all(
        "SELECT * FROM products WHERE chat_id = ? AND folder_id IS NULL ORDER BY created_ts DESC",
        (chat_id,)
    )


def list_all_products() -> Sequence:
    return fetch_all("SELECT * FROM products ORDER BY created_ts DESC")


def list_price_history(product_id: int):
    rows = fetch_all(
        "SELECT ts, price FROM price_history WHERE product_id = ? ORDER BY ts ASC", (product_id,)
    )
    if len(rows) > PRICE_SELECT_LIMIT:
        # downsample naive: pick every nth
        step = len(rows) // PRICE_SELECT_LIMIT
        rows = [r for i, r in enumerate(rows) if i % step == 0]
    return rows
