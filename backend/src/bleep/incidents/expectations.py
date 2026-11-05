""" "This is expected": a person teaches Bleep that what opened an incident is normal, in one tap.

Only what a later identical event can be judged by is recorded: a job's clean exit (its
container goes into its service's `exits`) or a host that sleeps (`may_sleep`). Anything else
has no expectation to record, and the quiet menu is the way to silence it. See
docs/design/watcher.md.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bleep.graph.deps import Approvals
from bleep.hosts.config import is_exit_name
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner, waits_for_host
from bleep.incidents.settings_store import SettingsRefused, SettingsStore
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, Signal

log = logging.getLogger(__name__)

Channel = Literal["telegram", "dashboard"]
# A plan running on the host is never cut short, so it cannot be called expected meanwhile.
RUNNING = ("executing", "verifying")
# The signals of a job's clean exit: its deaths, then down past grace or a loop of them.
EXIT_KINDS = ("container_died", "down_past_grace", "restart_loop")
CLOSED_NOTICE = "Closed: you said this is expected."
DECISION_TEXT = "This is expected."
_CHANNEL = {"telegram": "Telegram", "dashboard": "the dashboard"}


@dataclass(frozen=True)
class Expectation:
    """What "This is expected" records: `exits` entries for a service, or a host's `may_sleep`."""

    kind: Literal["exits", "may_sleep"]
    host_id: str
    service_id: str | None = None
    entries: tuple[str, ...] = ()
    subject_name: str = ""

    def sentence(self) -> str:
        if self.kind == "may_sleep":
            return (
                f"{self.subject_name} may sleep: I open nothing while it does not answer, "
                "and show it as asleep."
            )
        jobs = ", ".join(self.entries)
        return (
            f"{jobs} on {self.subject_name} is expected to exit: "
            "I stay quiet when it exits with 0, and a crash is still an incident."
        )


@dataclass(frozen=True)
class Answer:
    done: bool
    text: str


def expectation_for(
    incident: Incident, signals: list[Signal], registry: Registry
) -> Expectation | None:
    """What "This is expected" would record for this incident, or None when nothing applies: an
    unreachable host may sleep; containers that only exited with 0 are jobs meant to finish."""
    subject = incident.subject
    if subject.kind == "host":
        if not waits_for_host(incident, signals):
            return None
        host = next((h for h in registry.hosts() if h.config.id == subject.host_id), None)
        name = host.config.name if host is not None else subject.host_id
        return Expectation("may_sleep", subject.host_id, subject_name=name)
    service = registry.service(subject.service_id or "")
    deaths = [s for s in signals if s.kind == "container_died"]
    if service is None or not deaths or any(s.kind not in EXIT_KINDS for s in signals):
        return None
    if any(s.detail.get("exit_code") != 0 for s in deaths):
        return None  # a crash is never expected
    found = [_entry(s, registry) for s in deaths]
    if None in found:
        return None
    entries = tuple(dict.fromkeys(e for e in found if e is not None))
    return Expectation("exits", service.host_id, service.id, entries, service.name)


def _entry(death: Signal, registry: Registry) -> str | None:
    """The Compose service of the container when it has one (so every `compose run` of it
    matches), else its name; None when neither is a name `exits` can hold (a label is whatever
    the container was given, `*` included)."""
    name = death.container or ""
    label = death.detail.get("compose_service")
    if label is None:
        container = registry.container(death.subject.host_id, name)
        label = container.labels.get("com.docker.compose.service") if container else None
    for candidate in (label, name):
        if isinstance(candidate, str) and is_exit_name(candidate):
            return candidate
    return None


class Expectations:
    """Offers and applies "This is expected" for both channels, the same way."""

    def __init__(
        self,
        repo: IncidentRepo,
        runner: IncidentRunner,
        approvals: Approvals,
        settings: SettingsStore,
        registry: Registry,
        bus: EventBus,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.repo = repo
        self.runner = runner
        self.approvals = approvals
        self.settings = settings
        self.registry = registry
        self.bus = bus
        self.clock = clock

    async def offered(self, incident_id: str) -> Expectation | None:
        """What the action would record now; None when it is not offered (the incident is
        finished, a plan is approved or running on the host, or nothing applies)."""
        incident = await self.repo.get(incident_id)
        if incident is None or incident.status in (*TERMINAL_STATUSES, *RUNNING):
            return None
        latest = await self.approvals.current(incident_id)
        if latest is not None and latest.decision is not None and latest.decision.kind == "approve":
            return None
        return expectation_for(incident, await self.repo.signals(incident_id), self.registry)

    async def expected(self, incident_id: str, via: Channel) -> Answer:
        """Record the expectation, then reject a pending plan, then close the incident: a write
        that fails changes nothing. The reject is the same first-wins decision as any other
        answer; when a yes on another channel won it, the expectation stays recorded, the
        incident stays open with its plan, and the answer says so."""
        expectation = await self.offered(incident_id)
        if expectation is None:
            return Answer(False, "Nothing here can be marked as expected now.")
        service = self.registry.service(expectation.service_id or "")
        if expectation.kind == "exits" and service is None:
            return Answer(False, "Bleep does not see that service now; nothing was recorded.")
        try:
            if expectation.kind == "may_sleep":
                await self.settings.expect_sleep(expectation.host_id)
            elif service is not None:
                await self.settings.expect_exits(service, list(expectation.entries))
        except SettingsRefused as refused:
            log.warning("incident %s: expectation not recorded: %s", incident_id, refused)
            return Answer(False, "Bleep could not record that; nothing was changed.")
        text = f"You said this is expected. {expectation.sentence()}"
        pending = await self.approvals.current(incident_id)
        if pending is not None and pending.decision is None:
            decision = Decision(kind="reject", text=DECISION_TEXT, via=via, at=self.clock())
            if await self.approvals.resolve(incident_id, decision, pending.id) is None:
                return await self._lost(incident_id, text)
        await self.bus.publish(incident_id, "expected", {"text": text, "via": via})
        await self.runner.end(incident_id, CLOSED_NOTICE, spare=RUNNING)
        log.info("incident %s: marked expected on %s", incident_id, via)
        return Answer(True, text)

    async def _lost(self, incident_id: str, recorded: str) -> Answer:
        """A decision on another channel won the plan after the expectation was written."""
        latest = await self.approvals.current(incident_id)
        won = latest.decision if latest is not None else None
        where = f" on {_CHANNEL[won.via]}" if won is not None else ""
        await self.bus.publish(incident_id, "expected", {"text": recorded})
        return Answer(
            False, f"{recorded} The plan was already decided{where}, so the incident stays open."
        )
