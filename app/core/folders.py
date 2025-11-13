from typing import Optional, Sequence
from .db import fetch_one, fetch_all, execute


def create_folder(name: str, chat_id: int, emoji: str = "📁") -> int:
    """Create a new folder for organizing tracked products."""
    return execute(
        "INSERT INTO folders(name, emoji, chat_id) VALUES(?,?,?)",
        (name, emoji, chat_id)
    )


def get_folder(folder_id: int):
    """Get folder by ID."""
    return fetch_one("SELECT * FROM folders WHERE id = ?", (folder_id,))


def list_folders(chat_id: int) -> Sequence:
    """List all folders for a user."""
    return fetch_all(
        "SELECT * FROM folders WHERE chat_id = ? ORDER BY created_ts ASC",
        (chat_id,)
    )


def update_folder_emoji(folder_id: int, emoji: str) -> None:
    """Update folder emoji."""
    execute("UPDATE folders SET emoji = ? WHERE id = ?", (emoji, folder_id))


def rename_folder(folder_id: int, new_name: str) -> None:
    """Rename a folder."""
    execute("UPDATE folders SET name = ? WHERE id = ?", (new_name, folder_id))


def delete_folder(folder_id: int) -> None:
    """Delete a folder (products will have folder_id set to NULL)."""
    execute("DELETE FROM folders WHERE id = ?", (folder_id,))


def count_products_in_folder(folder_id: int) -> int:
    """Count products in a folder."""
    result = fetch_one("SELECT COUNT(*) as cnt FROM products WHERE folder_id = ?", (folder_id,))
    return result["cnt"] if result else 0
