"""Tells the human about an incident that has stopped moving. It only reports; it never acts."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from bleep.graph.deps import Notifier, SettingsSource
from bleep.graph.shown import shown_payload
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.schemas.incidents import Incident
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

TICK_S = 60  # how often `run` looks; a notice is at most this late

# Waiting for a human has reminders of its own, a paused run has already said so and retries by
# itself, and a waiting one waits for its host, which it said when it opened.
EXEMPT = ("awaiting_approval", "paused", "waiting")


class Watchdog:
    """One notice, on the dashboard and on the channels, for each stretch an unfinished incident
    does not move for `Budgets.stuck_after_min` (30 by default, read at every tick).

    An incident moves when its run publishes an event other than a notice, so a second stretch
    starts, and gets its own notice, once the incident has moved again. What was noticed is kept in
    memory: after a restart, an incident that is still stuck is noticed once more.
    """

    def __init__(
        self,
        incidents: IncidentRepo,
        bus: EventBus,
        notifier: Notifier,
        settings: SettingsSource,
        clock: Callable[[], datetime] = utcnow,
        skip: Callable[[str], bool] = lambda incident_id: False,
    ) -> None:
        self.incidents = incidents
        self.bus = bus
        self.notifier = notifier
        self.settings = settings
        self.clock = clock
        self.skip = skip
        self._noticed: dict[str, datetime] = {}  # incident id to the last move it was noticed at

    async def tick(self, now: datetime) -> list[str]:
        """Notice the incidents stuck at `now`; returns their ids."""
        limit = timedelta(minutes=self.settings.budgets().stuck_after_min)
        noticed = []
        open_ids = set()
        for incident in await self.incidents.unfinished():
            open_ids.add(incident.id)
            if incident.status in EXEMPT or self.skip(incident.id):
                self._noticed.pop(incident.id, None)
                continue
            try:
                moved = await self.bus.last_activity(incident.id) or incident.opened_at
                if now - moved < limit or self._noticed.get(incident.id) == moved:
                    continue
                await self._notice(incident, moved, now)
            except Exception:  # one incident's failure must not skip the others
                log.exception("watchdog could not check incident %s", incident.id)
                continue
            noticed.append(incident.id)
        for gone in set(self._noticed) - open_ids:
            del self._noticed[gone]
        return noticed

    async def _notice(self, incident: Incident, moved: datetime, now: datetime) -> None:
        self._noticed[incident.id] = moved
        minutes = int((now - moved).total_seconds() // 60)
        text = Shown.of("notice/stuck", minutes=str(minutes), status=incident.status)
        await self.bus.publish(incident.id, "notice", shown_payload(text))
        try:
            await self.notifier.notice(incident.id, text)
        except Exception:  # a channel outage must not hide the dashboard notice
            log.exception("watchdog notice for incident %s failed", incident.id)

    async def run(self) -> None:
        """Tick every `TICK_S` seconds until cancelled."""
        while True:
            try:
                await self.tick(self.clock())
            except Exception:
                log.exception("watchdog tick failed")
            await asyncio.sleep(TICK_S)
