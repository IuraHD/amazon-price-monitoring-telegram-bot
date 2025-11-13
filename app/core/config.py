import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    bot_token: str
    primary_chat_id: int | None
    price_refresh_minutes: int


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id_raw = os.getenv("TELEGRAM_PRIMARY_CHAT_ID")
    chat_id = int(chat_id_raw) if chat_id_raw and chat_id_raw.isdigit() else None
    refresh_raw = os.getenv("PRICE_REFRESH_MINUTES", "10")
    try:
        refresh = max(5, int(refresh_raw))
    except ValueError:
        refresh = 10
    return Settings(bot_token=token, primary_chat_id=chat_id, price_refresh_minutes=refresh)
