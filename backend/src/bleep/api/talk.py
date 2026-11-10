"""The dashboard's chat panel (#391): a message in, Bleep's answer out; a press of an offer's
button in, what it did out. A message changes nothing; only a press runs an offer, through the
same code as Telegram's buttons (`talk.actions`). See docs/design/app.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from bleep.api.deps import get_actions, get_parts
from bleep.schemas.talk import ChatAnswer, ChatMessage, ConfirmPress, ConfirmResult
from bleep.talk.actions import Actions
from bleep.wiring import AppParts

router = APIRouter(prefix="/talk", tags=["talk"])

Parts = Annotated[AppParts, Depends(get_parts)]
Acts = Annotated[Actions, Depends(get_actions)]


@router.post("/messages")
async def send_message(body: ChatMessage, parts: Parts, actions: Acts) -> ChatAnswer:
    if body.incident_id is not None and await parts.incidents.get(body.incident_id) is None:
        raise HTTPException(404, "no such incident")
    return await parts.panel.say(body.text.strip(), body.incident_id, actions)


@router.post("/confirms/{confirm_id}")
async def press_confirm(
    confirm_id: str, body: ConfirmPress, parts: Parts, actions: Acts
) -> ConfirmResult:
    return await parts.panel.press(confirm_id, body.yes, actions)
