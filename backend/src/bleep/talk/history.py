"""The last few turns of one conversation, kept in memory by the surface that holds it.

Each surface keeps its own (Telegram's bot one, the dashboard's panel its own): they share the
reading, not the transcript. In memory, not in the database: the turns only give the next
message its context, so a restart that drops them costs a little context, and the person's own
words, which may hold anything, are never written to disk (docs/design/notify.md). Each turn
keeps the incident it was about and when, so a message right after it goes on about that
incident (`talk.conversation.follow_up`, #422).
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

    def add(self, said: str, answered: str, incident_id: str | None = None) -> None:
        self._turns.append(
            Turn(said=said, answered=answered, incident_id=incident_id, at=self.clock())
        )

    def about(self, incident_id: str) -> None:
        """The conversation is about `incident_id` from now on: a look the person asked for in
        it opened that incident. Its last turn takes it, and the time."""
        if self._turns:
            last = self._turns[-1]
            self._turns[-1] = last.model_copy(
                update={"incident_id": incident_id, "at": self.clock()}
            )

    def recent(self) -> list[Turn]:
        """Oldest first."""
        return list(self._turns)
