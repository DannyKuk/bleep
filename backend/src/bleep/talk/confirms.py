"""The offers waiting for their button, kept by the surface that showed them (#402).

A button carries only the id of its offer; the offer itself stays here, so what a press runs is
exactly what was checked and shown, whatever a button's data says. An offer is taken once: a
second press, one after `CONFIRM_TTL` and one for an id never given are told apart and run
nothing. Held in memory, as the conversation's turns are (`talk.history`): an instruction holds
the person's own words, which are never written to disk before they are sent, and a restart
that drops the offers costs one more message.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from bleep.incidents.repository import utcnow
from bleep.schemas.talk import Offer

CONFIRM_TTL = timedelta(minutes=10)
KEPT = 100  # offers kept at once; answered ones go first, then the oldest
# A taken or expired offer is remembered this long after it ended, so a late press is told so.
REMEMBERED = timedelta(hours=1)

Refused = Literal["used", "expired", "unknown"]


@dataclass
class _Entry:
    offer: Offer
    expires: datetime
    taken: bool = False
    swept: bool = False  # its expiry was reported by `expired`


class Confirms:
    def __init__(self, clock: Callable[[], datetime] = utcnow, ttl: timedelta = CONFIRM_TTL):
        self.clock = clock
        self.ttl = ttl
        self._entries: dict[str, _Entry] = {}
        self._dropped: list[str] = []  # unanswered offers dropped for room, whose buttons stay

    def add(self, offer: Offer) -> str:
        """Keep `offer` until it is answered or expires; the id its buttons carry."""
        self._forget_old()
        while len(self._entries) >= KEPT:
            self._make_room()
        confirm_id = secrets.token_urlsafe(9)
        self._entries[confirm_id] = _Entry(offer, self.clock() + self.ttl)
        return confirm_id

    def take(self, confirm_id: str) -> Offer | Refused:
        """The offer to run, once; why not otherwise. No await between the look and the mark,
        so two presses can never both take it."""
        entry = self._entries.get(confirm_id)
        if entry is None:
            return "unknown"
        if entry.taken:
            return "used"
        if self.clock() >= entry.expires:
            return "expired"
        entry.taken = True
        return entry.offer

    def expired(self) -> list[str]:
        """The ids of the offers that expired unanswered since the last call, or were dropped
        to make room, once each, so their buttons can be removed."""
        now = self.clock()
        ids = [i for i, e in self._entries.items() if not (e.taken or e.swept) and now >= e.expires]
        for confirm_id in ids:
            self._entries[confirm_id].swept = True
        ids, self._dropped = [*self._dropped, *ids], []
        self._forget_old()
        return ids

    def _make_room(self) -> None:
        """Drop the oldest offer whose buttons are gone already (answered, or reported as
        expired); with none, the oldest of all, reported by `expired` so its buttons go too."""
        gone = next((i for i, e in self._entries.items() if e.taken or e.swept), None)
        if gone is None:
            gone = next(iter(self._entries))
            self._dropped.append(gone)
        del self._entries[gone]

    def _forget_old(self) -> None:
        now = self.clock()
        old = [
            i
            for i, e in self._entries.items()
            if (e.taken or e.swept) and now >= e.expires + REMEMBERED
        ]
        for confirm_id in old:  # an unswept one waits for `expired`, so its buttons still go
            del self._entries[confirm_id]
