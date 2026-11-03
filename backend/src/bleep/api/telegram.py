"""Whether Telegram is set up and polling. Never the token, never the whole chat id."""

from typing import Annotated

from fastapi import APIRouter, Depends

from bleep.api.deps import get_parts
from bleep.schemas.telegram import TelegramStatus
from bleep.wiring import AppParts

router = APIRouter(tags=["telegram"])


@router.get("/telegram/status")
async def telegram_status(parts: Annotated[AppParts, Depends(get_parts)]) -> TelegramStatus:
    return parts.notify.status()
