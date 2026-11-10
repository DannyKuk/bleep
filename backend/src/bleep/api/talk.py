"""The dashboard's chat panel (#391): a message in, Bleep's answer out; a press of an offer's
button in, what it did out. A message changes nothing; only a press runs an offer, through the
same code as Telegram's buttons (`talk.actions`). `GET /talk/log` exports the talk log of both
surfaces (#411). See docs/design/app.md."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response

from bleep.api.deps import get_actions, get_parts
from bleep.schemas.talk import (
    ChatAnswer,
    ChatMessage,
    ConfirmPress,
    ConfirmResult,
    TalkExchange,
)
from bleep.talk.actions import Actions
from bleep.talk.log import transcript
from bleep.wiring import AppParts

router = APIRouter(prefix="/talk", tags=["talk"])

Parts = Annotated[AppParts, Depends(get_parts)]
Acts = Annotated[Actions, Depends(get_actions)]


@router.post("/messages")
async def send_message(body: ChatMessage, parts: Parts, actions: Acts) -> ChatAnswer:
    if body.incident_id is not None and await parts.incidents.get(body.incident_id) is None:
        raise HTTPException(404, "no such incident")
    return await parts.panel.say(body.text, body.incident_id, actions)


@router.post("/confirms/{confirm_id}")
async def press_confirm(
    confirm_id: str, body: ConfirmPress, parts: Parts, actions: Acts
) -> ConfirmResult:
    return await parts.panel.press(confirm_id, body.yes, actions)


@router.get("/log", response_model=list[TalkExchange])
async def talk_log(parts: Parts, format: Literal["json", "md"] = "json") -> object:
    """The last messages of each chat with how Bleep read them; `md` as a transcript to save."""
    exchanges = parts.talk.log.exchanges()
    if format == "json":
        return exchanges
    now = parts.talk.log.clock()
    return Response(
        transcript(exchanges, now),
        media_type="text/markdown; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="bleep-chat-{now:%Y%m%d-%H%M}.md"'},
    )
