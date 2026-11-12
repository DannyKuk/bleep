"""What the watcher has seen of each host's containers that a listing alone does not say (#478).

Kept in memory only: a restart of Bleep forgets it, and the overview then falls back to what a
listing says. Each host's watcher updates it once per successful tick; the overview and the
manager read it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from bleep.schemas.docker import ContainerSummary, DockerEvent

# The states of a container that is not down, whose ids are no longer remembered.
_UP_STATES = ("running", "restarting")


@dataclass(frozen=True)
class HostSightings:
    """`first_listing`: when the host's watcher took its first listing. `stopped_on_purpose`:
    containers a `stop` event stopped and that have not started since. `down_at_start`:
    containers already down in that first listing that have not started since."""

    first_listing: datetime | None = None
    stopped_on_purpose: frozenset[str] = field(default_factory=frozenset)
    down_at_start: frozenset[str] = field(default_factory=frozenset)


class Sightings:
    def __init__(self) -> None:
        self._hosts: dict[str, HostSightings] = {}

    def of(self, host_id: str) -> HostSightings:
        return self._hosts.get(host_id, HostSightings())

    def forget(self, host_id: str) -> None:
        """The host was removed or now points at another machine: nothing seen holds there."""
        self._hosts.pop(host_id, None)

    def saw(
        self,
        host_id: str,
        at: datetime,
        events: Iterable[DockerEvent],
        listing: list[ContainerSummary],
        died: set[str],
    ) -> None:
        """One tick's events and listing. The host watcher's first tick is its baseline: what is
        down there, unless a death event of this tick explains it (`died`, which the watcher then
        waits on), was down before Bleep looked. A container is forgotten once it starts again
        or is no longer listed."""
        known = self.of(host_id)
        stopped = set(known.stopped_on_purpose)
        down_at_start = set(known.down_at_start)
        for event in sorted(events, key=lambda e: e.time):
            cid = event.container_id
            if cid is None or event.type != "container":
                continue
            if event.action == "stop":
                stopped.add(cid)
            elif event.action == "start":
                stopped.discard(cid)
                down_at_start.discard(cid)
        first_listing = known.first_listing
        if first_listing is None:
            first_listing = at
            down_at_start = {c.id for c in listing if c.state not in _UP_STATES} - died
        down = {c.id for c in listing if c.state not in _UP_STATES}
        self._hosts[host_id] = HostSightings(
            first_listing, frozenset(stopped & down), frozenset(down_at_start & down)
        )
