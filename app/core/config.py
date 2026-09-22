import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    primary_chat_id: int | None = None
    price_refresh_minutes: int = 10
    database_path: Path = Path(__file__).resolve().parents[1] / "data" / "tracker.db"


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Set TELEGRAM_BOT_TOKEN in the environment or .env file")
    raw = os.getenv("TELEGRAM_PRIMARY_CHAT_ID", "").strip()
    try:
        chat = int(raw) if raw else None
        minutes = int(os.getenv("PRICE_REFRESH_MINUTES", "10"))
    except ValueError as exc:
        raise ValueError("Chat ID and refresh interval must be integers") from exc
    if chat == 0 or not 5 <= minutes <= 1440:
        raise ValueError("Chat ID cannot be zero; refresh interval must be 5–1440 minutes")
    path = Path(os.getenv("DATABASE_PATH", str(Settings.database_path))).expanduser().resolve()
    return Settings(token, chat, minutes, path)
