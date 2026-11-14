"""Closing an incident whose service is healthy again on its own while its plan waits (#571).

The manager hands each judgement of a host's services to `RecoveryWatch.judge`, after every poll
tick and every round of URL checks: which of them are healthy now by the watcher's own reads
(`service_health` is `ok` and every URL of the service passed its last check, not gone stale).
For a service incident that waits for a yes (`awaiting_approval`) and is not a look a person
asked for (its service may have been healthy all along), the first such judgement
starts a hold and takes Verify's first reads (`verify.check_recovery`, injected as `check`). Any
judgement that is not healthy, a new signal on the incident, or a host that cannot be judged
(Bleep just woke, the host is asleep or out of contact) ends the hold. Once it has lasted
`HOLD`, Verify's checks run again against those first reads, so a container started again
meanwhile fails, and only when they pass is the incident closed through
`IncidentRunner.healthy_again`, which leaves it open if a decision won first.

Holds live in memory: a restart of Bleep starts them again. See docs/design/incidents.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from bleep.incidents.repository import IncidentRepo
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import VerificationResult
from bleep.schemas.incidents import LOOK_REQUESTED, Incident, Signal, Subject
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

# How long a service must stay healthy before its waiting incident closes: twenty poll ticks and
# ten rounds of URL checks, as long as a host must be out of contact to open an incident.
HOLD = timedelta(minutes=5)

RecoveryCheck = Callable[
    [Subject, list[Signal], dict[str, ContainerSummary]], Awaitable[VerificationResult]
]


@dataclass
class _Hold:
    host_id: str
    since: datetime
    signals: int  # how many signals the incident had when the hold started
    first_running: dict[str, ContainerSummary] = field(default_factory=dict)


class RecoveryWatch:
    """`name_of` gives the service's name as the notice shows it; `closed` hears of each
    incident this closed (the manager ends its containers' loops in the detector)."""

    def __init__(
        self,
        repo: IncidentRepo,
        runner: IncidentRunner,
        check: RecoveryCheck,
        clock: Callable[[], datetime],
        name_of: Callable[[Subject], str],
        closed: Callable[[Incident], Awaitable[None]] | None = None,
        hold: timedelta = HOLD,
    ) -> None:
        self.repo = repo
        self.runner = runner
        self.check = check
        self.clock = clock
        self.name_of = name_of
        self.closed = closed
        self.hold = hold
        self._holds: dict[str, _Hold] = {}  # incident id -> its hold
        self._locks: dict[str, asyncio.Lock] = {}  # host id -> one judgement at a time

    def forget_host(self, host_id: str) -> None:
        """End every hold on the host: nothing can be judged there now."""
        for incident_id in [i for i, h in self._holds.items() if h.host_id == host_id]:
            del self._holds[incident_id]

    async def judge(self, host_id: str, healthy: Collection[str]) -> None:
        """Judge the host's waiting service incidents by the ids of its services that are
        healthy now. Never raises: it must not cost the tick."""
        lock = self._locks.setdefault(host_id, asyncio.Lock())
        async with lock:  # the poll tick and the URL round judge apart, one after the other
            try:
                waiting = [
                    i
                    for i in await self.repo.open_on_host(host_id)
                    if i.status == "awaiting_approval" and i.subject.kind == "service"
                ]
            except Exception:
                log.exception("reading the waiting incidents of host %s failed", host_id)
                self.forget_host(host_id)
                return
            ids = {i.id for i in waiting}
            for incident_id, hold in list(self._holds.items()):
                if hold.host_id == host_id and incident_id not in ids:
                    del self._holds[incident_id]
            for incident in waiting:
                try:
                    await self._judge(incident, incident.subject.service_id in healthy)
                except Exception:  # its hold starts again; the others are judged as usual
                    log.exception("judging whether incident %s recovered failed", incident.id)
                    self._holds.pop(incident.id, None)

    async def _judge(self, incident: Incident, healthy: bool) -> None:
        if not healthy:
            self._holds.pop(incident.id, None)
            return
        now = self.clock()
        signals = await self.repo.signals(incident.id)
        if any(s.kind == LOOK_REQUESTED for s in signals):
            return  # a look's plan answers the person's question, not a failure that went away
        hold = self._holds.get(incident.id)
        if hold is None or len(signals) > hold.signals:  # a new failure starts the hold again
            hold = _Hold(incident.subject.host_id, now, len(signals))
            self._holds[incident.id] = hold
            await self.check(incident.subject, signals, hold.first_running)  # the first reads
            return
        if now - hold.since < self.hold:
            return
        del self._holds[incident.id]  # whatever comes of it, a new hold starts after
        result = await self.check(incident.subject, signals, hold.first_running)
        if not result.ok:
            log.info("incident %s: its service looked healthy, Verify's checks fail", incident.id)
            return
        text = Shown.of("notice/recovered", name=self.name_of(incident.subject))
        if await self.runner.healthy_again(incident.id, text) and self.closed is not None:
            await self.closed(incident)
