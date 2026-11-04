"""What the dashboard is told about Telegram: never the token, never the whole chat id."""

from datetime import datetime

from pydantic import BaseModel

from bleep.schemas.settings import SecretStatus


class TelegramStatus(BaseModel):
    """`GET /api/telegram/status`."""

    configured: bool  # a token and a chat id are set, so the bot polls
    chat_id: str | None  # masked to its last two characters at most
    last_poll: datetime | None  # the last getUpdates that reached Telegram
    token: SecretStatus
