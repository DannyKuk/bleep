"""The last few turns of one conversation, kept in memory by the surface that holds it.

Each surface keeps its own (Telegram's bot one, the dashboard's panel its own): they share the
reading, not the transcript. In memory, not in the database: the turns only give the next
message its context, so a restart that drops them costs a little context, and the person's own
words, which may hold anything, are never written to disk (docs/design/notify.md).
"""

from collections import deque

from bleep.schemas.talk import Turn

TURNS_KEPT = 4


class Turns:
    def __init__(self, keep: int = TURNS_KEPT) -> None:
        self._turns: deque[Turn] = deque(maxlen=keep)

    def add(self, said: str, answered: str) -> None:
        self._turns.append(Turn(said=said, answered=answered))

    def recent(self) -> list[Turn]:
        """Oldest first."""
        return list(self._turns)
