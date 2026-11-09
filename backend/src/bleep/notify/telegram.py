"""The Telegram bot: raw Bot API over httpx, long polling, one chat.

`TelegramClient` makes the calls; `TelegramBot` polls `getUpdates` and turns what the one
configured chat says into decisions. A button press is `approve:<approval id>` or
`reject:<approval id>`, `expected`, or `onit` and then `onit:<choice>` (a snooze). Any text is
answered through `talk` from Bleep's own state (#401), and changes nothing; a reply to a message
whose plan waits is still an instruction for it unless it reads as a question.
Decisions go through `approvals.resolve`, so the dashboard and Telegram race fairly: the winner
resumes the run, the loser is told which channel won. Updates from any other chat are dropped and
written to the guard log. The bot token is in every Bot API URL, so no error or log line here
carries a URL or an httpx message.
"""

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Literal, Protocol

import httpx

from bleep.graph.deps import Approvals
from bleep.guard.log import GuardLogger
from bleep.incidents.bus import EventBus
from bleep.incidents.expectations import RUNNING
from bleep.incidents.quiet import QuietStore, morning_of, quiet_target, snooze_until
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.notify.format import answer, phrase, shows_in_full, when
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.store.db import Database
from bleep.talk.conversation import Talk
from bleep.talk.history import Turns

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
POLL_TIMEOUT_S = 30  # getUpdates long-poll length
POLL_REQUEST_TIMEOUT_S = POLL_TIMEOUT_S + 10  # the long poll, plus room for the answer
REQUEST_TIMEOUT_S = 10  # every other call: a hung send must not hold the Approval node long
BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 60.0
INSTRUCTION_MAX_CHARS = 1000
TALK_WAITING_MAX = 3  # messages being answered at once; one more is told Bleep is busy
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


class ExpectAnswer(Protocol):
    done: bool
    text: str


class Expecting(Protocol):
    """The part of `incidents.expectations.Expectations` the bot and the notifier use."""

    async def offered(self, incident_id: str) -> Any: ...

    async def expected(
        self, incident_id: str, via: Literal["telegram", "dashboard"]
    ) -> ExpectAnswer: ...


EXPECTED_VERB = "expected"  # the "It's expected" button's data; the incident is the message's
EXPECTED_LABEL = "It's expected"
CALLBACK_TEXT_MAX = 200  # Telegram's limit for a button's answer
# "🤫 I'm on it" is `onit`; its choices are `onit:1h`, `onit:4h` and `onit:morning:YYYYMMDD`,
# the morning the menu showed. The incident is the message's.
ON_IT_VERB = "onit"
ON_IT_LABEL = "🤫 I'm on it"
ON_IT_NOTICE = "Snoozed from Telegram: you said you are on it."
SNOOZE_AHEAD_MAX = timedelta(days=2)  # no button carries a later morning

Row = list[tuple[str, str]]  # (label, callback data) buttons side by side


def _no_zone() -> tzinfo | None:
    return None


@dataclass(frozen=True)
class OnIt:
    """What "🤫 I'm on it" needs: the incidents, the quiet store that snoozes an incident's
    subject (its listener, the watcher, then ends the incident as Quiet), the timeline, and
    `current_zone`, which gives the time zone "until 08:00" is in. It is asked at each use, so a
    zone chosen on the settings page applies to the next message (#400); None when none is
    chosen: UTC, and the times say so."""

    incidents: IncidentRepo
    quiet: QuietStore
    bus: EventBus
    current_zone: Callable[[], tzinfo | None] = _no_zone

    @property
    def zone(self) -> tzinfo | None:
        return self.current_zone()


def snooze_buttons(now: datetime, zone: tzinfo | None) -> Row:
    """The choices a press of "🤫 I'm on it" shows: 1 h, 4 h, until 08:00. The morning's
    button carries its date, so a press after it has passed snoozes nothing."""
    morning = snooze_until("morning", now, zone or UTC)
    day = morning.astimezone(zone or UTC).date()
    return [
        (phrase("duration_h", n="1"), f"{ON_IT_VERB}:1h"),
        (phrase("duration_h", n="4"), f"{ON_IT_VERB}:4h"),
        (
            phrase("on_it_morning", when=when(morning, now, zone)),
            f"{ON_IT_VERB}:morning:{day:%Y%m%d}",
        ),
    ]


def chosen_until(choice: str, now: datetime, zone: tzinfo | None) -> datetime | None:
    """When the snooze a choice's data asks for ends; None for data no button carries."""
    if choice in ("1h", "4h"):
        return snooze_until("1h" if choice == "1h" else "4h", now, zone or UTC)
    kind, _, day = choice.partition(":")
    if kind != "morning" or len(day) != 8 or not day.isdigit():
        return None
    try:
        morning = date(int(day[:4]), int(day[4:6]), int(day[6:]))
    except ValueError:
        return None
    return morning_of(morning, zone or UTC)


def _kept_rows(markup: Any) -> list[Row]:
    """The rows of a pressed message's buttons, as Telegram sent them back with the press,
    without the "🤫 I'm on it" row (its button or its choices)."""
    rows: list[Row] = []
    for row in (markup or {}).get("inline_keyboard", []) if isinstance(markup, dict) else []:
        kept = [
            (button["text"], button["callback_data"])
            for button in row
            if isinstance(button, dict)
            and isinstance(button.get("text"), str)
            and isinstance(button.get("callback_data"), str)
        ]
        if kept and not any(data.partition(":")[0] == ON_IT_VERB for _, data in kept):
            rows.append(kept)
    return rows


class TelegramClient:
    """Bot API calls the bot and the notifier make. `http` is the caller's `httpx.AsyncClient`."""

    def __init__(self, token: str, http: httpx.AsyncClient) -> None:
        self._base = f"{API}/bot{token}"
        self.http = http

    def same_bot(self, other: TelegramClient) -> bool:
        return hmac.compare_digest(self._base, other._base)

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
        buttons: Row | None = None,
        reply_to: int | None = None,
        markdown: bool = True,
        silent: bool = False,
        more: list[Row] | None = None,
    ) -> int:
        """Send `text` (MarkdownV2 unless `markdown` is False); returns the message id. A reply
        whose message was deleted is sent anyway, as a message of its own. A `silent` message
        arrives without a sound or a ping. `buttons` is a row; `more` are rows under it."""
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if markdown:
            params["parse_mode"] = "MarkdownV2"
        if silent:
            params["disable_notification"] = True
        rows = [buttons or [], *(more or [])]
        if any(rows):
            params["reply_markup"] = _keyboard(rows)
        if reply_to is not None:
            params["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        return (await self.call("sendMessage", **params))["message_id"]

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def clear_buttons(self, chat_id: str, message_id: int) -> None:
        await self.set_buttons(chat_id, message_id, [])

    async def set_buttons(self, chat_id: str, message_id: int, rows: list[Row]) -> None:
        """Replace a message's buttons with `rows`; none removes them."""
        await self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=_keyboard(rows),
        )


def _keyboard(rows: list[Row]) -> dict[str, Any]:
    """Rows of (label, callback data) buttons; an empty row is left out."""
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row] for row in rows if row
        ]
    }


class MessageLog:
    """`telegram_messages`: which incident each message sent about one is about, the plan for a
    proposal or a reminder (an empty approval id for every other message: it asks about no plan),
    and whether it carries buttons. An incident's lowest message id is its opener."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        message_id: int,
        incident_id: str,
        approval_id: str,
        at: datetime,
        buttons: bool = True,
        end: bool = False,
    ) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO telegram_messages (message_id, incident_id, approval_id,"
                " sent_at, buttons, is_end) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, incident_id, approval_id, at.isoformat(), int(buttons), int(end)),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def ids_for_incident(self, incident_id: str) -> list[int]:
        """The incident's messages that carry buttons."""
        return await self._ids("incident_id = ? AND buttons", incident_id)

    async def first_for(self, incident_id: str) -> int | None:
        """The incident's opener: the first of its messages that reached the chat."""
        ids = await self._ids("incident_id = ?", incident_id, "MIN")
        return ids[0] if ids else None

    async def ended(self, incident_id: str) -> bool:
        """Whether a message that ended the incident was sent."""
        return bool(await self._ids("incident_id = ? AND is_end", incident_id, "MAX"))

    async def last_for(self, incident_id: str) -> int | None:
        ids = await self._ids("incident_id = ?", incident_id, "MAX")
        return ids[0] if ids else None

    async def ids_for_approval(self, approval_id: str) -> list[int]:
        return await self._ids("approval_id = ?", approval_id)

    async def _ids(self, where: str, value: str, pick: str = "") -> list[int]:
        """The ids of the matching rows; with `pick` (MIN or MAX) only that one, if any."""
        column = f"{pick}(message_id)" if pick else "message_id"
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {column} AS message_id FROM telegram_messages WHERE {where}", (value,)
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [row["message_id"] for row in rows if row["message_id"] is not None]

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
        on_it: OnIt | None = None,
        talk: Talk | None = None,
    ) -> None:
        self.chat_id = str(chat_id)
        self.approvals = approvals
        self.runner = runner
        self.client = TelegramClient(token, http)
        self.messages = messages
        self.guard_log = guard_log
        self.clock = clock
        self._sleep = sleep
        self.expectations: Expecting | None = None
        self.on_it = on_it
        self.talk = talk
        self.turns = Turns()  # this chat's last turns, for the next message's context
        self._talking: set[asyncio.Task[None]] = set()
        self._talk_turn = asyncio.Lock()
        self._offset: int | None = None
        self.last_poll: datetime | None = None  # the last getUpdates that Telegram answered

    def continue_from(self, other: TelegramBot) -> None:
        """Go on from where `other` stopped when it is the same bot (the same token): Telegram
        would otherwise send again what `other` handled but had not yet confirmed."""
        if other.client.same_bot(self.client):
            self._offset = other._offset

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
        if verb == EXPECTED_VERB and known is not None and self.expectations is not None:
            await self._expected(callback_id, message_id, known[0])
            return
        if verb == ON_IT_VERB and known is not None and self.on_it is not None:
            await self._on_it(callback, *known, choice=approval_id)
            return
        if verb not in ("approve", "reject") or not approval_id or known is None:
            await self.client.answer_callback(callback_id, answer("answer_unknown"))
            return
        kind: Literal["approve", "reject"] = "approve" if verb == "approve" else "reject"
        incident_id = known[0]
        if await self._closed(incident_id, approval_id):  # before why a yes is not offered here
            await self.client.answer_callback(callback_id, answer("answer_closed"))
            await self.client.clear_buttons(self.chat_id, message_id)
            return
        if kind == "approve" and await self._refused_plan(incident_id, approval_id):
            await self.client.answer_callback(callback_id, answer("answer_refused_plan"))
            return
        if kind == "approve" and not await self._shown_in_full(incident_id, approval_id):
            # No Approve button was sent for it; a press that arrives anyway is refused.
            await self.client.answer_callback(callback_id, answer("answer_dashboard_only"))
            return
        decision = Decision(kind=kind, via="telegram", at=self.clock())
        await self._decide(callback_id, message_id, incident_id, approval_id, decision)

    async def _expected(self, callback_id: str, message_id: int, incident_id: str) -> None:
        """ "It's expected": record it and close the incident, as the dashboard's button does."""
        assert self.expectations is not None
        result = await self.expectations.expected(incident_id, "telegram")
        text = result.text
        if len(text) > CALLBACK_TEXT_MAX:
            text = text[: CALLBACK_TEXT_MAX - 1] + "…"
        await self.client.answer_callback(callback_id, text)
        if result.done:  # a failed press leaves Approve and Reject to a plan that still waits
            await self.client.clear_buttons(self.chat_id, message_id)

    async def _on_it(
        self, callback: dict[str, Any], incident_id: str, approval_id: str, choice: str
    ) -> None:
        """A press shows the choices in place of the "🤫 I'm on it" row; a choice snoozes the
        incident's subject, as the dashboard's "I'm on it" does, and the quiet store's listener
        ends the incident as Quiet. Not on a finished incident, nor while a step of an approved
        plan runs on the host, which is never cut short."""
        assert self.on_it is not None
        callback_id, message = callback["id"], callback["message"]
        incident = await self.on_it.incidents.get(incident_id)
        target = quiet_target(incident.subject) if incident is not None else None
        now, zone = self.clock(), self.on_it.zone
        until = chosen_until(choice, now, zone) if choice else None
        unknown = choice and (until is None or until > now + SNOOZE_AHEAD_MAX)
        if incident is None or target is None or unknown:
            await self.client.answer_callback(callback_id, answer("answer_unknown"))
            return
        if incident.status in TERMINAL_STATUSES:
            await self.client.answer_callback(callback_id, answer("answer_on_it_ended"))
            await self._edit(message["message_id"], [])
            return
        if incident.status in RUNNING:
            await self.client.answer_callback(callback_id, answer("answer_on_it_running"))
            return
        if until is None or until <= now:  # the first press, or a morning that has passed
            said = "answer_on_it_how_long" if until is None else "answer_on_it_passed"
            await self.client.answer_callback(callback_id, answer(said))
            await self._show_choices(message, incident_id, approval_id, now)
            return
        await self.on_it.quiet.set(*target, "snooze", until)
        await self.on_it.bus.publish(incident_id, "notice", {"text": ON_IT_NOTICE})
        said = answer("answer_on_it", until=when(until, now, zone))
        await self.client.answer_callback(callback_id, said)

    async def _show_choices(
        self, message: dict[str, Any], incident_id: str, approval_id: str, now: datetime
    ) -> None:
        """The choices in place of the "🤫 I'm on it" row; the rows above it (a plan's Approve
        and Reject, "It's expected") stay while their question is open."""
        assert self.on_it is not None
        message_id = message["message_id"]
        choices = snooze_buttons(now, self.on_it.zone)
        kept = _kept_rows(message.get("reply_markup"))
        open_plan = await self._plan_open(incident_id, approval_id)
        await self._edit(message_id, [*kept, choices] if open_plan else [choices])
        # The incident can end, or its plan be decided, while the edit is on its way; their
        # clearing may have come first, and the edit must not put buttons back.
        incident = await self.on_it.incidents.get(incident_id)
        if incident is None or incident.status in TERMINAL_STATUSES:
            await self._edit(message_id, [])
        elif kept and open_plan and not await self._plan_open(incident_id, approval_id):
            await self._edit(message_id, [choices])

    async def _plan_open(self, incident_id: str, approval_id: str) -> bool:
        """Whether the plan a message asks about still waits; a message about none asks
        nothing that closes before its incident ends."""
        if not approval_id:
            return True
        current = await self.approvals.current(incident_id)
        return (
            current is not None
            and current.id == approval_id
            and current.decision is None
            and not current.closed
        )

    async def _edit(self, message_id: int, rows: list[Row]) -> None:
        """Set a message's buttons; a message that shows them already (a second tap) is fine."""
        try:
            await self.client.set_buttons(self.chat_id, message_id, rows)
        except TelegramError as error:
            if "message is not modified" not in str(error):
                raise

    async def _closed(self, incident_id: str, approval_id: str) -> bool:
        current = await self.approvals.current(incident_id)
        return current is not None and current.id == approval_id and current.closed

    async def _refused_plan(self, incident_id: str, approval_id: str) -> bool:
        current = await self.approvals.current(incident_id)
        return current is not None and current.id == approval_id and current.proposal.refused

    async def _shown_in_full(self, incident_id: str, approval_id: str) -> bool:
        current = await self.approvals.current(incident_id)
        if current is None or current.id != approval_id:
            return True  # `_resolve` answers a stale or unknown plan in its own words
        return shows_in_full(current.proposal)

    async def _text(self, message: dict[str, Any]) -> None:
        """With `talk`, what the person wrote is answered (`_answer`). Without it, a reply to a
        message about an incident instructs its plan (`_instruct`) and any other text is told
        how to give one."""
        reply = message["message_id"]
        text = message["text"].strip()
        reply_to = message.get("reply_to_message", {}).get("message_id")
        known = await self.messages.get(reply_to) if reply_to else None
        if self.talk is None and known is None:
            await self._say(answer("answer_no_reply"), reply)
            return
        if len(text) > INSTRUCTION_MAX_CHARS:
            await self._say(answer("answer_too_long", limit=str(INSTRUCTION_MAX_CHARS)), reply)
            return
        if self.talk is None:
            assert known is not None
            await self._say(await self._instruct(*known, text), reply)
            return
        if known is not None and (known[1] or await self._waiting(known[0])):
            # A reply a plan may take is handled here, in order, so a press that came after it
            # (an Approve) waits behind it, as it did before the reply was read by a model.
            await self._answer(text, reply, known, to_plan=True)
            return
        if len(self._talking) >= TALK_WAITING_MAX:
            await self._say(answer("answer_talk_busy"), reply)
            return
        task = asyncio.create_task(self._answer_safely(text, reply, known), name="telegram talk")
        self._talking.add(task)
        task.add_done_callback(self._talking.discard)

    async def _answer_safely(self, text: str, reply: int, known: tuple[str, str] | None) -> None:
        """`_answer` in the background, so a slow model never holds a button press behind a
        message that can change nothing; one at a time in the order they came. An error is
        logged, as `poll_once` logs one, and never reaches the poller."""
        try:
            async with self._talk_turn:
                await self._answer(text, reply, known, to_plan=False)
        except TelegramError as error:
            log.warning("telegram: could not answer a message: %s", error)
        except Exception:
            log.exception("telegram: answering a message failed")

    async def _answer(
        self, text: str, reply: int, known: tuple[str, str] | None, to_plan: bool
    ) -> None:
        """Answer what the person wrote through `talk`, which reads and never changes anything;
        a reply to a message about an incident is about that incident. Until replies are
        confirmed with a button (#402), a reply a plan would take (`to_plan`: the plan its
        message showed, or the one waiting on its incident) still instructs that plan, as
        before, unless it reads as a question; so does one Bleep could not read."""
        assert self.talk is not None
        incident_id = known[0] if known is not None else None
        said = await self.talk.reply(text, incident_id, self.turns.recent())
        if known is not None and to_plan and said.kind != "question":
            note = await self._instruct(*known, text)
        else:
            note = said.answer
        self.turns.add(text, note)
        await self._say(note, reply)

    async def _say(self, text: str, reply_to: int) -> None:
        await self.client.send_message(self.chat_id, text, reply_to=reply_to, markdown=False)

    async def settle(self) -> None:
        """Wait for the messages being answered (tests, and a stop that lets them finish)."""
        while self._talking:
            await asyncio.gather(*list(self._talking), return_exceptions=True)

    async def stop_talking(self) -> None:
        """Cut off the messages being answered: the bot is stopping."""
        for task in list(self._talking):
            task.cancel()
        await asyncio.gather(*list(self._talking), return_exceptions=True)

    async def _instruct(self, incident_id: str, approval_id: str, text: str) -> str:
        """Instruct a plan with `text`: the one the message showed (so a replaced plan is
        answered as stale), or, for a message that showed none, the plan that waits for a
        decision on its incident now. Returns what to tell the person."""
        if not approval_id:
            approval_id = await self._waiting(incident_id)
        if not approval_id:
            return answer("answer_not_waiting")
        decision = Decision(kind="instruct", text=text, via="telegram", at=self.clock())
        return await self._resolve(incident_id, approval_id, decision)

    async def _waiting(self, incident_id: str) -> str:
        """The id of the incident's plan that waits for a decision now; empty when none does."""
        current = await self.approvals.current(incident_id)
        if current is None or current.decision is not None or current.closed:
            return ""
        return current.id

    async def _decide(
        self, callback_id: str, message_id: int, incident_id: str, approval_id: str, d: Decision
    ) -> None:
        note = await self._resolve(incident_id, approval_id, d)
        await self.client.answer_callback(callback_id, note)
        await self.client.clear_buttons(self.chat_id, message_id)

    async def _resolve(self, incident_id: str, approval_id: str, decision: Decision) -> str:
        """Try to win the approval; the text to tell the human either way."""
        before = await self.approvals.current(incident_id)
        won = await self.approvals.resolve(incident_id, decision, approval_id)
        if won is not None:
            await self.runner.resume(incident_id, won, approval_id)
            refused = before is not None and before.id == approval_id and before.proposal.refused
            if refused and decision.kind == "reject":
                return answer("answer_let_go")
            return answer(f"answer_{_PAST[decision.kind]}")
        current = await self.approvals.current(incident_id)
        if current is not None and current.id == approval_id and current.decision is not None:
            via = current.decision.via
            return answer("answer_already_decided", channel=_CHANNEL_NAMES.get(via, via))
        if current is not None and current.id == approval_id and current.closed:
            return answer("answer_closed")
        if current is not None and current.id != approval_id:
            return answer("answer_stale")
        return answer("answer_not_waiting")


_PAST = {"approve": "approved", "reject": "rejected", "instruct": "instructed"}
