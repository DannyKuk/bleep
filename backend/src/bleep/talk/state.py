"""The snapshot of Bleep's own state a chat answer comes from, built by code.

Hosts and services come through `HealthSource`, which `wiring` fills with the watcher's overview
(the dashboard's own views), so `talk` never imports `watcher`. Incidents, their plans and their
reports come from the stores the dashboard reads. Nothing here reaches a host.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Protocol

from bleep.guard.redact import redacted_proposal
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.schemas.api import HostView, QuietView, ServiceView
from bleep.schemas.graph import ApprovalRecord
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, IncidentEvent
from bleep.schemas.talk import (
    ContainerLine,
    HostLine,
    IncidentFacts,
    IncidentLine,
    QuietLine,
    ServiceLine,
    SignalLine,
    Snapshot,
    StepLine,
)

RECENT = timedelta(hours=24)  # ended incidents the snapshot lists
RECENT_SCAN = 50  # the newest incidents looked at for those
CAUSE_CHARS = 200
NOTE_CHARS = 200
NOTES_KEPT = 5
SIGNALS_KEPT = 5


class HealthSource(Protocol):
    """The hosts and services as the overview shows them (`watcher.overview.Overview`)."""

    async def hosts(self) -> list[HostView]: ...

    async def services(self) -> list[ServiceView]: ...


class Plans(Protocol):
    """The part of `incidents.approvals.ApprovalStore` the snapshot reads."""

    async def current(self, incident_id: str) -> ApprovalRecord | None: ...


class StateReader(Protocol):
    """What `Talk` reads: the whole snapshot, and one incident in detail."""

    async def snapshot(self) -> Snapshot: ...

    async def incident(self, incident_id: str) -> IncidentFacts | None: ...


def _line(text: str, limit: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


class BleepState:
    """Reads Bleep's state for a chat answer. `zone` gives the time zone answers are in, read at
    each message (None: UTC)."""

    def __init__(
        self,
        health: HealthSource,
        incidents: IncidentRepo,
        plans: Plans,
        bus: EventBus,
        zone: Callable[[], tzinfo | None] = lambda: None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.health = health
        self.incidents = incidents
        self.plans = plans
        self.bus = bus
        self.zone = zone
        self.clock = clock

    def _at(self, at: datetime | None) -> str | None:
        if at is None:
            return None
        return at.astimezone(self.zone() or UTC).strftime("%Y-%m-%d %H:%M")

    def _quiet(self, quiet: QuietView | None, own_scope: str) -> QuietLine | None:
        if quiet is None:
            return None
        return QuietLine(
            mode=quiet.mode,
            until=self._at(quiet.until),
            since=self._at(quiet.since) or "",
            through_host=quiet.scope != own_scope,
        )

    async def snapshot(self) -> Snapshot:
        now = self.clock()
        hosts = await self.health.hosts()
        services = await self.health.services()
        open_incidents = await self.incidents.unfinished()
        recent = [
            i
            for i in await self.incidents.list(limit=RECENT_SCAN)
            if i.status in TERMINAL_STATUSES and (i.closed_at or i.opened_at) >= now - RECENT
        ]
        names = {i.id: i.short_name or i.id for i in open_incidents}
        return Snapshot(
            now=self._at(now) or "",
            zone=str(self.zone() or "UTC"),
            hosts=[self._host(h) for h in hosts],
            services=[self._service(s, names) for s in services],
            open_incidents=[await self._incident_line(i) for i in open_incidents],
            ended_last_24h=[await self._incident_line(i) for i in recent],
        )

    def _host(self, host: HostView) -> HostLine:
        return HostLine(
            id=host.id,
            name=host.name,
            health=host.health,
            reachable=host.reachable,
            last_seen=self._at(host.last_seen),
            quiet=self._quiet(host.quiet, "host"),
        )

    def _service(self, service: ServiceView, names: dict[str, str]) -> ServiceLine:
        return ServiceLine(
            id=service.id,
            name=service.name,
            host=service.host_id,
            health=service.health,
            containers=[
                ContainerLine(
                    name=c.name,
                    state=c.state,
                    health=c.health,
                    exit_code=c.exit_code,
                    restarts=c.restart_count,
                )
                for c in service.containers
            ],
            quiet=self._quiet(service.quiet, "service"),
            open_incident=names.get(service.open_incident_id or ""),
        )

    async def _incident_line(self, incident: Incident) -> IncidentLine:
        facts = await self._facts(incident, detail=False)
        return IncidentLine.model_validate(facts.model_dump(include=set(IncidentLine.model_fields)))

    async def incident(self, incident_id: str) -> IncidentFacts | None:
        incident = await self.incidents.get(incident_id)
        if incident is None:
            return None
        return await self._facts(incident, detail=True)

    async def _facts(self, incident: Incident, detail: bool) -> IncidentFacts:
        events = await self.bus.history(incident.id)
        report = _last(events, "report")
        ended = incident.status in TERMINAL_STATUSES
        notes = [e.payload.get("text") for e in events if e.kind == "notice"]
        notes = [_line(n, NOTE_CHARS) for n in notes if isinstance(n, str) and n.strip()]
        outcome = _text(report, "outcome") or (notes[-1] if ended and notes else None)
        record = await self.plans.current(incident.id)
        waiting = record is not None and record.decision is None and not record.closed and not ended
        facts = IncidentFacts(
            id=incident.id,
            name=incident.short_name,
            title=incident.title,
            subject=incident.subject.service_id or incident.subject.host_id,
            status=incident.status,
            opened=self._at(incident.opened_at) or "",
            ended=self._at(incident.closed_at) if ended else None,
            plan_waiting=waiting,
            outcome=_line(outcome, CAUSE_CHARS) if outcome else None,
            cause=_line(cause, CAUSE_CHARS) if (cause := _text(report, "cause")) else None,
        )
        if detail:
            signals = await self.incidents.signals(incident.id)
            facts.signals = [
                SignalLine(kind=s.kind, container=s.container, at=self._at(s.at) or "")
                for s in signals[:SIGNALS_KEPT]
            ]
            if waiting and record is not None:
                shown = redacted_proposal(record.proposal)
                facts.plan = [
                    StepLine(action=s.action, target=s.target, host=s.host_id) for s in shown.steps
                ]
            facts.notes = notes[-NOTES_KEPT:]
            summary = _text(report, "summary")
            facts.summary = _line(summary, CAUSE_CHARS * 2) if summary else None
        return facts


def _last(events: list[IncidentEvent], kind: str) -> dict[str, Any] | None:
    found = [e.payload for e in events if e.kind == kind]
    return found[-1] if found else None


def _text(payload: dict[str, Any] | None, key: str) -> str | None:
    value = (payload or {}).get(key)
    return value if isinstance(value, str) and value.strip() else None
