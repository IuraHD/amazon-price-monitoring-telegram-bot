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


def search_products_by_name(chat_id: int, search_term: str) -> Sequence:
    """Search products by name (case-insensitive)."""
    return fetch_all("""
        SELECT p.*, f.name as folder_name, f.emoji as folder_emoji
        FROM products p
        LEFT JOIN folders f ON p.folder_id = f.id
        WHERE p.chat_id = ? AND LOWER(p.title) LIKE LOWER(?)
        ORDER BY p.created_ts DESC
    """, (chat_id, f"%{search_term}%"))


def search_products_by_asin(chat_id: int, asin: str) -> Sequence:
    """Search products by ASIN."""
    return fetch_all("""
        SELECT p.*, f.name as folder_name, f.emoji as folder_emoji
        FROM products p
        LEFT JOIN folders f ON p.folder_id = f.id
        WHERE p.chat_id = ? AND p.asin LIKE ?
        ORDER BY p.created_ts DESC
    """, (chat_id, f"%{asin}%"))


def filter_products_by_price_range(chat_id: int, min_price: float, max_price: float) -> Sequence:
    """Filter products by price range."""
    return fetch_all("""
        SELECT p.*, f.name as folder_name, f.emoji as folder_emoji
        FROM products p
        LEFT JOIN folders f ON p.folder_id = f.id
        WHERE p.chat_id = ? AND p.last_price IS NOT NULL 
        AND p.last_price BETWEEN ? AND ?
        ORDER BY p.last_price ASC
    """, (chat_id, min_price, max_price))


def get_products_statistics(chat_id: int) -> dict:
    """Get statistics about tracked products."""
    total_products = fetch_one("SELECT COUNT(*) as cnt FROM products WHERE chat_id = ?", (chat_id,))
    total_with_price = fetch_one(
        "SELECT COUNT(*) as cnt FROM products WHERE chat_id = ? AND last_price IS NOT NULL",
        (chat_id,)
    )
    total_value = fetch_one(
        "SELECT SUM(last_price) as total FROM products WHERE chat_id = ? AND last_price IS NOT NULL",
        (chat_id,)
    )
    alerts_enabled = fetch_one(
        "SELECT COUNT(*) as cnt FROM products WHERE chat_id = ? AND alerts_enabled = 1",
        (chat_id,)
    )
    
    return {
        "total_products": total_products["cnt"] if total_products else 0,
        "total_with_price": total_with_price["cnt"] if total_with_price else 0,
        "total_value": total_value["total"] if total_value and total_value["total"] else 0.0,
        "alerts_enabled": alerts_enabled["cnt"] if alerts_enabled else 0,
    }


def toggle_all_alerts(chat_id: int, enabled: bool) -> None:
    """Toggle alerts for all products of a user."""
    execute("UPDATE products SET alerts_enabled = ? WHERE chat_id = ?", (1 if enabled else 0, chat_id))


def get_price_trends(chat_id: int) -> dict:
    """Get products with recent price changes."""
    # Get products with at least 2 price history entries
    products = fetch_all("""
        SELECT p.id, p.asin, p.title, p.last_price
        FROM products p
        WHERE p.chat_id = ? AND p.last_price IS NOT NULL
    """, (chat_id,))
    
    decreased = []
    increased = []
    
    for prod in products:
        history = fetch_all(
            "SELECT price FROM price_history WHERE product_id = ? ORDER BY ts DESC LIMIT 2",
            (prod["id"],)
        )
        if len(history) >= 2:
            current = history[0]["price"]
            previous = history[1]["price"]
            if current < previous:
                decreased.append({**dict(prod), "change": previous - current})
            elif current > previous:
                increased.append({**dict(prod), "change": current - previous})
    
    return {
        "decreased": decreased,
        "increased": increased
    }
