"""Reminders for approvals nobody has answered. A reminder only asks again; it never decides."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from bleep.graph.deps import Notifier, SettingsSource
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.repository import IncidentRepo, utcnow

log = logging.getLogger(__name__)

TICK_S = 60  # how often `run` looks; a reminder is at most this late


class ReminderLoop:
    """Re-sends each pending approval of an open incident once per reminder period.

    The period is `Budgets.approval_reminder_min` (60 by default), read at every tick. The n-th
    reminder is due n periods after the request, so they go out at 60, 120, 180 min; after
    downtime only one goes out, not one for each period missed.
    """

    def __init__(
        self,
        approvals: ApprovalStore,
        incidents: IncidentRepo,
        notifier: Notifier,
        settings: SettingsSource,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.approvals = approvals
        self.incidents = incidents
        self.notifier = notifier
        self.settings = settings
        self.clock = clock

    async def tick(self, now: datetime) -> list[str]:
        """Send the reminders due at `now`; returns the ids of the approvals reminded."""
        period = timedelta(minutes=self.settings.budgets().approval_reminder_min)
        reminded = []
        for record in await self.approvals.pending():
            due = int((now - record.requested_at) / period)
            if due <= record.reminders:
                continue
            # Counted before it is sent: a crash in between loses one reminder, never repeats one.
            if not await self.approvals.mark_reminded(record.id, due):
                continue  # decided, or counted by another tick, in the meantime
            incident = await self.incidents.get(record.incident_id)
            if incident is None:
                continue
            try:
                await self.notifier.reminder(incident, record.request)
            except Exception:  # one failed channel must not stop the other reminders
                log.exception("reminder for incident %s failed", record.incident_id)
                continue
            reminded.append(record.id)
        return reminded

    async def run(self) -> None:
        """Tick every `TICK_S` seconds until cancelled."""
        while True:
            try:
                await self.tick(self.clock())
            except Exception:
                log.exception("reminder tick failed")
            await asyncio.sleep(TICK_S)
