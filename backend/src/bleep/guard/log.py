"""What the guard reports to, kept apart so tools and the watcher can share it."""

from typing import Protocol


class GuardLogger(Protocol):
    """Where guard events (redactions, blocked actions) are recorded."""

    def record(self, incident_id: str, kind: str, detail: str) -> None: ...
