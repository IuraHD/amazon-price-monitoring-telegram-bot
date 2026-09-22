import regex
from .db import connection, fetch_all, fetch_one


def clean_name(name):
    name = " ".join(name.split())
    if not name or len(name) > 50:
        raise ValueError("Folder names must contain 1–50 characters.")
    return name


def clean_emoji(value):
    value = value.strip()
    if value == "/skip":
        return "📁"
    parts = regex.findall(r"\X", value)
    if (
        len(parts) != 1
        or len(value) > 20
        or not regex.search(r"\p{Extended_Pictographic}|\p{Regional_Indicator}|\u20e3", value)
    ):
        raise ValueError("Send one complete emoji, or /skip for the default.")
    return value


def list_folders(chat_id):
    return fetch_all(
        """SELECT f.*,COUNT(s.id) AS count FROM folders f LEFT JOIN subscriptions s
        ON s.folder_id=f.id AND s.chat_id=f.chat_id WHERE f.chat_id=? GROUP BY f.id ORDER BY f.name COLLATE NOCASE""",
        (chat_id,),
    )


def get_folder(chat_id, folder_id):
    return fetch_one("SELECT * FROM folders WHERE id=? AND chat_id=?", (folder_id, chat_id))


def create_folder(chat_id, name, emoji="📁"):
    with connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM folders WHERE chat_id=?", (chat_id,)).fetchone()[0] >= 100:
            raise ValueError("Limit reached: 100 folders per chat.")
        return conn.execute(
            "INSERT INTO folders(chat_id,name,emoji) VALUES(?,?,?)",
            (chat_id, clean_name(name), clean_emoji(emoji)),
        ).lastrowid


def edit_folder(chat_id, folder_id, *, name=None, emoji=None):
    with connection() as conn:
        if name is not None:
            cursor = conn.execute(
                "UPDATE folders SET name=? WHERE id=? AND chat_id=?", (clean_name(name), folder_id, chat_id)
            )
        else:
            cursor = conn.execute(
                "UPDATE folders SET emoji=? WHERE id=? AND chat_id=?",
                (clean_emoji(emoji), folder_id, chat_id),
            )
        if not cursor.rowcount:
            raise ValueError("Folder no longer exists.")


def delete_folder(chat_id, folder_id):
    with connection() as conn:
        conn.execute("DELETE FROM folders WHERE id=? AND chat_id=?", (folder_id, chat_id))
