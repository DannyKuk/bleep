"""Closing an incident whose service is healthy again on its own while its plan waits (#571).

The manager hands each judgement of a host's services to `RecoveryWatch.judge`, after every poll
tick and every round of URL checks: which of them are healthy now by the watcher's own reads
(`service_health` is `ok` and every URL of the service passed its last check, not gone stale).
For a service incident that waits for a yes (`awaiting_approval`) and is not a look a person
asked for (its service may have been healthy all along), the first such judgement starts a hold.
Any judgement that is not healthy, a new signal on the incident, or a host that cannot be judged
(Bleep just woke, the host is asleep or out of contact) ends the hold. Once it has lasted
`HOLD`, Verify's checks run against the first reads taken when it started, so a container
started again meanwhile fails, and only when they pass is the incident closed through
`IncidentRunner.healthy_again`, which leaves it open if a decision won first.

`judge` only reads the database and keeps the holds: the engine and URL reads Verify's checks
make (`Checks`, from `graph/nodes/verify.py`) run in one background task per hold, so a slow
host never holds up the watcher's tick. A hold that ends cancels its task; a cancel that lands
after the close is committed still lets the notice and the message go out (`healthy_again`
shields them).

Holds live in memory: a restart of Bleep starts them again. See docs/design/incidents.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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

FirstReads = Callable[[Subject, list[Signal]], Awaitable[dict[str, ContainerSummary]]]
Check = Callable[
    [Subject, list[Signal], dict[str, ContainerSummary]], Awaitable[VerificationResult]
]


@dataclass(frozen=True)
class Checks:
    """Verify's checks for a held service: `first_reads` when the hold starts (`recovery_reads`)
    and `check` against them when it is over (`check_recovery`)."""

    first_reads: FirstReads
    check: Check


@dataclass
class _Hold:
    incident: Incident
    since: datetime
    signals: list[Signal]  # the incident's signals when the hold started
    first_running: dict[str, ContainerSummary] | None = None  # None until they are read
    task: asyncio.Task[None] | None = None


class RecoveryWatch:
    """`name_of` gives the service's name as the notice shows it."""

    def __init__(
        self,
        repo: IncidentRepo,
        runner: IncidentRunner,
        checks: Checks,
        clock: Callable[[], datetime],
        name_of: Callable[[Subject], str],
        hold: timedelta = HOLD,
    ) -> None:
        self.repo = repo
        self.runner = runner
        self.checks = checks
        self.clock = clock
        self.name_of = name_of
        self.hold = hold
        self._holds: dict[str, _Hold] = {}  # incident id -> its hold
        self._locks: dict[str, asyncio.Lock] = {}  # host id -> one judgement at a time
        self._tasks: set[asyncio.Task[None]] = set()  # every check in flight

    def forget_host(self, host_id: str) -> None:
        """End every hold on the host: nothing can be judged there now."""
        for incident_id, hold in list(self._holds.items()):
            if hold.incident.subject.host_id == host_id:
                self._end(incident_id)

    async def stop(self) -> None:
        """End every hold and wait for the checks in flight to stop (the app's shutdown)."""
        self._holds.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        """Wait for the checks in flight (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def judge(self, host_id: str, healthy: Collection[str]) -> None:
        """Judge the host's waiting service incidents by the ids of its services that are
        healthy now. Only reads the database; never raises: it must not cost the tick."""
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
                if hold.incident.subject.host_id == host_id and incident_id not in ids:
                    self._end(incident_id)
            for incident in waiting:
                try:
                    await self._judge(incident, incident.subject.service_id in healthy)
                except Exception:  # its hold starts again; the others are judged as usual
                    log.exception("judging whether incident %s recovered failed", incident.id)
                    self._end(incident.id)

    async def _judge(self, incident: Incident, healthy: bool) -> None:
        if not healthy:
            self._end(incident.id)
            return
        signals = await self.repo.signals(incident.id)
        if any(s.kind == LOOK_REQUESTED for s in signals):
            return  # a look's plan answers the person's question, not a failure that went away
        hold = self._holds.get(incident.id)
        if hold is not None and len(signals) > len(hold.signals):  # a new failure
            self._end(incident.id)
            hold = None
        if hold is None:
            hold = _Hold(incident, self.clock(), signals)
            self._holds[incident.id] = hold
            self._spawn(hold, self._first_reads(hold))
            return
        busy = hold.task is not None and not hold.task.done()
        if busy or hold.first_running is None or self.clock() - hold.since < self.hold:
            return
        self._spawn(hold, self._confirm(hold))

    def _spawn(self, hold: _Hold, work: Coroutine[Any, Any, None]) -> None:
        hold.task = asyncio.create_task(work, name=f"recovery {hold.incident.id}")
        self._tasks.add(hold.task)
        hold.task.add_done_callback(self._tasks.discard)

    def _end(self, incident_id: str) -> None:
        hold = self._holds.pop(incident_id, None)
        if hold is not None and hold.task is not None:
            hold.task.cancel()

    async def _first_reads(self, hold: _Hold) -> None:
        incident = hold.incident
        try:
            hold.first_running = await self.checks.first_reads(incident.subject, hold.signals)
        except Exception:  # the next healthy judgement starts a new hold
            log.exception("reading incident %s's service for its hold failed", incident.id)
            self._drop(hold)

    async def _confirm(self, hold: _Hold) -> None:
        incident = hold.incident
        try:
            result = await self.checks.check(
                incident.subject, hold.signals, hold.first_running or {}
            )
        except Exception:
            log.exception("checking whether incident %s recovered failed", incident.id)
            self._drop(hold)
            return
        self._drop(hold)  # whatever comes of it, a new hold starts after
        if not result.ok:
            log.info("incident %s: its service looked healthy, Verify's checks fail", incident.id)
            return
        text = Shown.of("notice/recovered", name=self.name_of(incident.subject))
        await self.runner.healthy_again(incident.id, text)

    def _drop(self, hold: _Hold) -> None:
        """Forget `hold` from inside its own task, unless a newer one took its place."""
        if self._holds.get(hold.incident.id) is hold:
            del self._holds[hold.incident.id]
