"""The last few turns of one conversation, kept in memory by the surface that holds it.

Each surface keeps its own (Telegram's bot one, the dashboard's panel its own): they share the
reading, not the transcript. In memory, not in the database: the turns only give the next
message its context, so a restart that drops them costs a little context, and the person's own
words, which may hold anything, are never written to disk (docs/design/notify.md). Each turn
keeps the incident it was about and when the conversation took it up, so a message right after
it is read with that incident's facts (`talk.conversation.follow_up`, #422).
"""

from collections import deque
from collections.abc import Callable
from datetime import datetime

from bleep.incidents.repository import utcnow
from bleep.schemas.talk import Turn

TURNS_KEPT = 4


class Turns:
    def __init__(self, keep: int = TURNS_KEPT, clock: Callable[[], datetime] = utcnow) -> None:
        self._turns: deque[Turn] = deque(maxlen=keep)
        self.clock = clock

    def add(
        self, said: str, answered: str, incident_id: str | None = None, named: bool = False
    ) -> None:
        """A turn about `incident_id`: `named` when the message named it (a reply, the panel's
        incident chip), which takes the incident up now; else a follow-up, which keeps the time
        the last turn took it up, so the window it is read in never slides."""
        last = self._turns[-1] if self._turns else None
        if incident_id is None:
            since = None
        elif named:
            since = self.clock()
        else:
            same = last is not None and last.incident_id == incident_id
            since = last.incident_at if same and last is not None else None
        self._turns.append(
            Turn(said=said, answered=answered, incident_id=incident_id, incident_at=since)
        )

    def about(self, incident_id: str) -> None:
        """The conversation takes up `incident_id` now: a look the person asked for in it opened
        that incident, or found it open. Its last turn takes it."""
        if self._turns:
            last = self._turns[-1]
            self._turns[-1] = last.model_copy(
                update={"incident_id": incident_id, "incident_at": self.clock()}
            )

    def recent(self) -> list[Turn]:
        """Oldest first."""
        return list(self._turns)
