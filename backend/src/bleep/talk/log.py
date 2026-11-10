"""The talk log (#411): the last messages of each chat with how Bleep read them, so an answer
that was off can be reviewed from the real conversation, not from memory.

`Talk.reply` adds an entry for each message it reads, with the reading, what the checks made of
it and what it cost; the surface then adds what it showed (`shown`) and the press of the offer's
buttons (`pressed`), found by the offer's id. In memory, one ring per surface, like the turns
(`talk.history`): a restart empties it, and the person's words, which may hold anything, are
never written to disk, and are kept only as Bleep read them. `GET /api/talk/log` reads it, as
JSON or as the Markdown transcript `transcript` builds (docs/design/app.md).
"""

import itertools
import re
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

from bleep import prompts
from bleep.incidents.repository import utcnow
from bleep.schemas.talk import TalkExchange, TalkLink, TalkPress, TalkSurface
from bleep.talk.confirms import NO_LABEL, YES_LABEL, yes_label

EXCHANGES_KEPT = 100  # per surface
SURFACES: tuple[TalkSurface, ...] = ("panel", "telegram")


class TalkLog:
    def __init__(self, clock: Callable[[], datetime] = utcnow, keep: int = EXCHANGES_KEPT) -> None:
        self.clock = clock
        self._rings: dict[TalkSurface, deque[TalkExchange]] = {
            surface: deque(maxlen=keep) for surface in SURFACES
        }
        self._ids = itertools.count(1)

    def next_id(self) -> int:
        return next(self._ids)

    def add(self, exchange: TalkExchange) -> None:
        self._rings[exchange.surface].append(exchange)

    def shown(
        self,
        surface: TalkSurface,
        exchange_id: int | None,
        answer: str,
        links: Sequence[TalkLink] = (),
        confirm_id: str | None = None,
    ) -> None:
        """What the surface showed for the message: a check of its own (binding an offer to its
        plan) may have answered other than the reading did, and an offer has its buttons' id."""
        entry = next((e for e in self._rings[surface] if e.id == exchange_id), None)
        if entry is not None:
            entry.answer, entry.links, entry.confirm_id = answer, list(links), confirm_id

    def pressed(self, surface: TalkSurface, confirm_id: str, yes: bool) -> TalkPress | None:
        """Record the press that took the offer, before what it runs is awaited, so a second
        press answered meanwhile can never take its place; the surface fills in `line` once
        the press is answered. Only a press that took its offer is recorded."""
        entry = next((e for e in self._rings[surface] if e.confirm_id == confirm_id), None)
        if entry is None or entry.press is not None:
            return None
        entry.press = TalkPress(at=self.clock(), yes=yes)
        return entry.press

    def exchanges(self) -> list[TalkExchange]:
        """Each surface's entries, oldest first, the panel's before Telegram's."""
        return [e.model_copy(deep=True) for s in SURFACES for e in self._rings[s]]


async def answered(press: TalkPress | None, answer: str | Awaitable[str]) -> str:
    """The line a press is answered with, awaited if it runs something, and kept on `press`. A
    run that fails is kept as the fixed line naming the error's type, and raised again."""
    try:
        line = answer if isinstance(answer, str) else await answer
    except Exception as error:
        if press is not None:  # by type only: an error's message may quote a host or a key
            press.line = _line("log_press_failed", error=type(error).__name__)
        raise
    if press is not None:
        press.line = line
    return line


def _line(name: str, **values: str) -> str:
    return prompts.load(f"talk/{name}", **values).strip()


def _quoted(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines() or [""])


def _code(json: str) -> str:
    """`json` as inline code, fenced by more backticks than any run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", json)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {json} {fence}"


def _cost(exchange: TalkExchange) -> str:
    usage = exchange.usage
    if usage is None:
        return _line("log_no_call")
    usd = f"${usage.usd:.5f}" if usage.usd is not None else _line("log_cost_unknown")
    return _line(
        "log_usage",
        model=usage.model,
        calls=str(usage.calls),
        tokens_in=str(usage.tokens_in),
        tokens_out=str(usage.tokens_out),
        usd=usd,
    )


def _details(exchange: TalkExchange) -> list[str]:
    if exchange.reading is not None:
        reading = exchange.reading.model_dump_json(exclude_none=True, exclude_defaults=True)
        lines = [_line("log_reading", reading=_code(reading))]
    else:
        lines = [_line("log_unread", error=exchange.error or "")]
    if exchange.offer is not None:
        offer = exchange.offer.model_dump_json(exclude_none=True)
        lines.append(_line("log_offer", offer=_code(offer)))
    if exchange.refused is not None:
        lines.append(_line("log_refused", line=exchange.refused))
    if exchange.links:
        lines.append(_line("log_links", links=", ".join(link.label for link in exchange.links)))
    if exchange.press is not None:
        yes = yes_label(exchange.offer) if exchange.offer is not None else YES_LABEL
        button = yes if exchange.press.yes else NO_LABEL
        said = exchange.press.line or _line("log_press_running")
        lines.append(_line("log_press", button=button, line=said))
    lines.append(_cost(exchange))
    return lines


def _entry(exchange: TalkExchange) -> str:
    about = _line("log_about", incident=exchange.incident_id) if exchange.incident_id else ""
    return _line(
        "log_exchange",
        at=exchange.at.isoformat(timespec="seconds") + (f" · {about}" if about else ""),
        text=_quoted(exchange.text),
        answer=_quoted(exchange.answer),
        details="\n".join(_details(exchange)),
    )


def transcript(exchanges: Sequence[TalkExchange], now: datetime) -> str:
    """The log as a Markdown transcript a person reads: each surface's messages, oldest first."""
    parts = [_line("log_head", at=now.isoformat(timespec="seconds"), kept=str(EXCHANGES_KEPT))]
    for surface in SURFACES:
        mine = [_entry(e) for e in exchanges if e.surface == surface]
        parts += [_line(f"log_{surface}"), *(mine or [_line("log_empty")])]
    return "\n\n".join(parts) + "\n"
