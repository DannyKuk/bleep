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
from bleep.graph.shown import english, shown_payload
from bleep.hosts.config import is_exit_name
from bleep.hosts.discovery import compose_service
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner, waits_for_host
from bleep.incidents.settings_store import SettingsRefused, SettingsStore
from bleep.schemas.graph import Decision
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, Signal
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

Channel = Literal["telegram", "dashboard"]
# A plan running on the host is never cut short, so it cannot be called expected meanwhile.
RUNNING = ("executing", "verifying")
# The signals of a job's clean exit: its deaths, then down past grace or a loop of them.
EXIT_KINDS = ("container_died", "down_past_grace", "restart_loop")
CLOSED_NOTICE = Shown.of("notice/expected")
DECISION_TEXT = Shown.of("expected/decision")
CLOSED_MEANWHILE = Shown.of("expected/closed_meanwhile")
_CHANNEL = {
    "telegram": Shown.of("channel/on_telegram"),
    "dashboard": Shown.of("channel/on_dashboard"),
}


@dataclass(frozen=True)
class Expectation:
    """What "This is expected" records: `exits` entries for a service, or a host's `may_sleep`."""

    kind: Literal["exits", "may_sleep"]
    host_id: str
    service_id: str | None = None
    entries: tuple[str, ...] = ()
    subject_name: str = ""

    def said(self) -> Shown:
        """What it records, in one sentence."""
        if self.kind == "may_sleep":
            return Shown.of("expected/may_sleep", name=self.subject_name)
        return Shown.of("expected/exits", jobs=", ".join(self.entries), name=self.subject_name)

    def sentence(self) -> str:
        return english(self.said())


@dataclass(frozen=True)
class Answer:
    """What a press of "This is expected" is told, shown in the person's language."""

    done: bool
    said: Shown

    @property
    def text(self) -> str:
        return english(self.said)


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
        label = compose_service(container.labels) if container else None
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
        answer; when another decision (a yes, an instruction or a no) won it, also one that
        landed while the settings were written, the expectation stays recorded, the incident
        follows that decision, and the answer says so. It is done only when this call closed the
        incident."""
        expectation = await self.offered(incident_id)
        if expectation is None:
            return Answer(False, Shown.of("expected/not_offered"))
        service = self.registry.service(expectation.service_id or "")
        if expectation.kind == "exits" and service is None:
            return Answer(False, Shown.of("expected/no_service"))
        try:
            if expectation.kind == "may_sleep":
                await self.settings.expect_sleep(expectation.host_id)
            elif service is not None:
                await self.settings.expect_exits(service, list(expectation.entries))
        except SettingsRefused as refused:
            log.warning("incident %s: expectation not recorded: %s", incident_id, refused)
            return Answer(False, Shown.of("expected/not_recorded"))
        text = Shown.of("expected/said", sentence=expectation.said())
        pending = await self.approvals.current(incident_id)
        if pending is not None and pending.decision is None:
            decision = Decision(
                kind="reject",
                text=english(DECISION_TEXT),
                via=via,
                at=self.clock(),
                shown=DECISION_TEXT,
            )
            if await self.approvals.resolve(incident_id, decision, pending.id) is None:
                return await self._lost(incident_id, text, via)
        elif pending is not None:  # a decision won while the settings were written
            return await self._lost(incident_id, text, via)
        await self.bus.publish(incident_id, "expected", shown_payload(text, via=via))
        if not await self.runner.end(incident_id, CLOSED_NOTICE, ending="expected", spare=RUNNING):
            # A plan started running on the host meanwhile, or the incident closed on its own.
            return Answer(False, text.then(Shown.of("expected/not_closed")))
        log.info("incident %s: marked expected on %s", incident_id, via)
        return Answer(True, text)

    async def _lost(self, incident_id: str, recorded: Shown, via: Channel) -> Answer:
        """A decision on another channel won the plan after the expectation was written, or the
        incident was closed from outside (quiet, a removed host) before anyone answered it."""
        latest = await self.approvals.current(incident_id)
        await self.bus.publish(incident_id, "expected", shown_payload(recorded, via=via))
        if latest is not None and latest.closed:
            return Answer(False, recorded.then(CLOSED_MEANWHILE))
        won = latest.decision if latest is not None else None
        if won is None:
            return Answer(False, recorded.then(Shown.of("expected/decided")))
        decided = Shown.of("expected/decided_on", channel=_CHANNEL[won.via])
        return Answer(False, recorded.then(decided))
