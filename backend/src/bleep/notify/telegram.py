"""The Telegram bot: raw Bot API over httpx, long polling, one chat.

`TelegramClient` makes the calls; `TelegramBot` polls `getUpdates` and turns what the one
configured chat says into decisions. A button press is `approve:<approval id>` or
`reject:<approval id>`; a reply to a proposal message is an instruction for that incident.
Decisions go through `approvals.resolve`, so the dashboard and Telegram race fairly: the winner
resumes the run, the loser is told which channel won. Updates from any other chat are dropped and
written to the guard log. The bot token is in every Bot API URL, so no error or log line here
carries a URL or an httpx message.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

import httpx

from bleep.graph.deps import Approvals
from bleep.guard.log import GuardLogger
from bleep.incidents.repository import utcnow
from bleep.notify.format import answer
from bleep.schemas.graph import Decision
from bleep.store.db import Database

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
POLL_TIMEOUT_S = 30  # getUpdates long-poll length
POLL_REQUEST_TIMEOUT_S = POLL_TIMEOUT_S + 10  # the long poll, plus room for the answer
REQUEST_TIMEOUT_S = 10  # every other call: a hung send must not hold the Approval node long
BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 60.0
INSTRUCTION_MAX_CHARS = 1000
GUARD_INCIDENT = "telegram"  # guard_log rows that belong to no incident
_CHANNEL_NAMES = {"dashboard": "the dashboard", "telegram": "Telegram"}


def mask_chat_id(chat_id: str) -> str:
    """The chat id as the dashboard shows it: a fixed mask and, for an id long enough that they
    say little, its last two characters."""
    return "••••" + (chat_id[-2:] if len(chat_id) > 4 else "")


class TelegramError(Exception):
    """A Bot API call failed. The message never holds the URL, so never the token."""


class Resumer(Protocol):
    """The part of `IncidentRunner` the bot uses."""

    async def resume(self, incident_id: str, decision: Decision, approval_id: str) -> Any: ...


class TelegramClient:
    """Bot API calls the bot and the notifier make. `http` is the caller's `httpx.AsyncClient`."""

    def __init__(self, token: str, http: httpx.AsyncClient) -> None:
        self._base = f"{API}/bot{token}"
        self.http = http

    async def call(self, method: str, *, wait_s: float = REQUEST_TIMEOUT_S, **params: Any) -> Any:
        """One Bot API call; `params` are the method's own (getUpdates has a `timeout` too)."""
        try:
            response = await self.http.post(f"{self._base}/{method}", json=params, timeout=wait_s)
            body = response.json()
        except (httpx.HTTPError, ValueError, RuntimeError) as error:  # RuntimeError: client closed
            raise TelegramError(f"{method}: {type(error).__name__}") from None
        if response.status_code != 200 or not body.get("ok"):
            raise TelegramError(f"{method}: {response.status_code} {body.get('description', '')}")
        return body["result"]

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": POLL_TIMEOUT_S,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        return await self.call("getUpdates", wait_s=POLL_REQUEST_TIMEOUT_S, **params)

    async def send_message(
        self,
        chat_id: str,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
        reply_to: int | None = None,
        markdown: bool = True,
    ) -> int:
        """Send `text` (MarkdownV2 unless `markdown` is False); returns the message id."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if markdown:
            params["parse_mode"] = "MarkdownV2"
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]
            }
        if reply_to is not None:
            params["reply_parameters"] = {"message_id": reply_to}
        return (await self.call("sendMessage", **params))["message_id"]

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def clear_buttons(self, chat_id: str, message_id: int) -> None:
        await self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup={"inline_keyboard": []},
        )


class MessageLog:
    """`telegram_messages`: which incident and plan each proposal message is about."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, message_id: int, incident_id: str, approval_id: str, at: datetime) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO telegram_messages (message_id, incident_id, approval_id,"
                " sent_at) VALUES (?, ?, ?, ?)",
                (message_id, incident_id, approval_id, at.isoformat()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get(self, message_id: int) -> tuple[str, str] | None:
        """(incident id, approval id) the message asked about."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT incident_id, approval_id FROM telegram_messages WHERE message_id = ?",
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return (row["incident_id"], row["approval_id"]) if row else None


class TelegramBot:
    """Polls Telegram and applies the chat's answers. Start it with `poll_forever()`."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        approvals: Approvals,
        runner: Resumer,
        http: httpx.AsyncClient,
        *,
        messages: MessageLog,
        guard_log: GuardLogger | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.chat_id = str(chat_id)
        self.approvals = approvals
        self.runner = runner
        self.client = TelegramClient(token, http)
        self.messages = messages
        self.guard_log = guard_log
        self.clock = clock
        self._sleep = sleep
        self._offset: int | None = None
        self.last_poll: datetime | None = None  # the last getUpdates that Telegram answered

    async def poll_forever(self) -> None:
        """Long-poll until cancelled; an outage or any other error backs off from 1 s to 60 s and
        never ends it, because a dead poller would silently stop Telegram approvals."""
        backoff = BACKOFF_START_S
        while True:
            try:
                await self.poll_once()
                backoff = BACKOFF_START_S
                continue
            except TelegramError as error:
                log.warning("telegram: %s; retrying in %.0f s", error, backoff)
            except Exception as error:  # the type only: a message could quote the token's URL
                name = type(error).__name__
                log.error("telegram: polling failed (%s); retrying in %.0f s", name, backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def poll_once(self) -> None:
        """One `getUpdates` call and the handling of what it returned."""
        updates = await self.client.get_updates(self._offset)
        self.last_poll = self.clock()
        for update in updates:
            self._offset = update["update_id"] + 1  # confirmed even if handling fails below
            try:
                await self.handle(update)
            except TelegramError as error:
                log.warning("telegram: could not answer an update: %s", error)
            except Exception:
                log.exception("telegram: handling an update failed")

    async def handle(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        message = update.get("message") or (callback or {}).get("message")
        chat = (message or {}).get("chat", {}).get("id")
        if str(chat) != self.chat_id:
            kind = "callback" if callback else "message" if message else "update"
            self._ignore(f"{kind} from chat {chat}")
            return
        if callback:
            await self._button(callback)
        elif message and message.get("text"):
            await self._text(message)

    def _ignore(self, what: str) -> None:
        log.info("telegram: ignored %s", what)
        if self.guard_log is not None:
            self.guard_log.record(GUARD_INCIDENT, "foreign_chat", f"ignored {what}")

    async def _button(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        message_id = callback["message"]["message_id"]
        verb, _, approval_id = str(callback.get("data", "")).partition(":")
        known = await self.messages.get(message_id)
        if verb not in ("approve", "reject") or not approval_id or known is None:
            await self.client.answer_callback(callback_id, answer("answer_unknown"))
            return
        kind: Literal["approve", "reject"] = "approve" if verb == "approve" else "reject"
        incident_id = known[0]
        decision = Decision(kind=kind, via="telegram", at=self.clock())
        await self._decide(callback_id, message_id, incident_id, approval_id, decision)

    async def _text(self, message: dict[str, Any]) -> None:
        reply_to = message.get("reply_to_message", {}).get("message_id")
        known = await self.messages.get(reply_to) if reply_to else None
        reply = message["message_id"]
        if known is None:
            await self.client.send_message(
                self.chat_id, answer("answer_no_reply"), reply_to=reply, markdown=False
            )
            return
        text = message["text"].strip()
        if len(text) > INSTRUCTION_MAX_CHARS:
            note = answer("answer_too_long", limit=str(INSTRUCTION_MAX_CHARS))
            await self.client.send_message(self.chat_id, note, reply_to=reply, markdown=False)
            return
        incident_id, approval_id = known
        decision = Decision(kind="instruct", text=text, via="telegram", at=self.clock())
        note = await self._resolve(incident_id, approval_id, decision)
        await self.client.send_message(self.chat_id, note, reply_to=reply, markdown=False)

    async def _decide(
        self, callback_id: str, message_id: int, incident_id: str, approval_id: str, d: Decision
    ) -> None:
        note = await self._resolve(incident_id, approval_id, d)
        await self.client.answer_callback(callback_id, note)
        await self.client.clear_buttons(self.chat_id, message_id)

    async def _resolve(self, incident_id: str, approval_id: str, decision: Decision) -> str:
        """Try to win the approval; the text to tell the human either way."""
        won = await self.approvals.resolve(incident_id, decision, approval_id)
        if won is not None:
            await self.runner.resume(incident_id, won, approval_id)
            return answer(f"answer_{_PAST[decision.kind]}")
        current = await self.approvals.current(incident_id)
        if current is not None and current.id == approval_id and current.decision is not None:
            via = current.decision.via
            return answer("answer_already_decided", channel=_CHANNEL_NAMES.get(via, via))
        if current is not None and current.id != approval_id:
            return answer("answer_stale")
        return answer("answer_not_waiting")


_PAST = {"approve": "approved", "reject": "rejected", "instruct": "instructed"}
