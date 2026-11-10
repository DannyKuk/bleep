"""The snapshot of Bleep's own state a chat answer comes from, built by code.

Hosts and services come through `HealthSource`, which `wiring` fills with the watcher's overview
(the dashboard's own views), so `talk` never imports `watcher`. Incidents, their plans and their
reports come from the stores the dashboard reads. Nothing here reaches a host.
"""

import re
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
    CheckLine,
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
# What the run checked (#422): the last calls and checks, each part cut. A result line is the
# first line of the tool's guarded output as the event stores it, still host text.
CHECKS_KEPT = 12
TARGET_CHARS = 120
RESULT_CHARS = 160
# The argument that says what a read tool looked at: a container, a Coolify app, an allowed host
# command, a search, a repository; and how far back it looked.
_TARGET_ARGS = ("container", "app", "command", "query", "repo")
_MINUTES_ARGS = ("minutes", "since_minutes")
_GUARD_NOTE = re.compile(r"\[… (?:[0-9]+ chars cut|repeated [0-9]+ times)\]")
# The first line of a tool's output that is the same whatever it found (`tools/docker_read.py`).
_TOOL_HEADS = {
    "list_containers": re.compile(r"name \| service \| state \| health \| image \| restarts"),
    "inspect_container": re.compile(r"name: \S+"),
}
# Tools whose first line never tells what was found: most allowed host commands start with a
# column header (`df -P`, `free -b`, `vm_stat`), so a host command keeps no result.
_NO_RESULT = {"host_command"}
# Names and titles came from hosts and labels; each is cut so one long label cannot crowd out
# the rest, and the lists are cut so a large setup still fits (`Snapshot.left_out` counts the
# rest). Services that need attention are listed first, so they are the last to go.
NAME_CHARS = 80
TITLE_CHARS = 120
HOSTS_KEPT = 20
SERVICES_KEPT = 60
CONTAINERS_KEPT = 6
OPEN_KEPT = 20
ENDED_KEPT = 15
_CALM = ("ok", "done", "stopped", "asleep")


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


def _calm(service: ServiceView) -> bool:
    """Whether a service needs no attention: it sorts after those that do."""
    return service.health in _CALM and service.quiet is None and not service.open_incident_id


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
        services = sorted(services, key=_calm)
        left_out = {
            "hosts": len(hosts) - HOSTS_KEPT,
            "services": len(services) - SERVICES_KEPT,
            "containers": sum(max(len(s.containers) - CONTAINERS_KEPT, 0) for s in services),
            "open_incidents": len(open_incidents) - OPEN_KEPT,
            "ended_last_24h": len(recent) - ENDED_KEPT,
        }
        return Snapshot(
            now=self._at(now) or "",
            zone=str(self.zone() or "UTC"),
            hosts=[self._host(h) for h in hosts[:HOSTS_KEPT]],
            services=[self._service(s, names) for s in services[:SERVICES_KEPT]],
            open_incidents=[await self._incident_line(i) for i in open_incidents[-OPEN_KEPT:]],
            ended_last_24h=[await self._incident_line(i) for i in recent[:ENDED_KEPT]],
            left_out={part: n for part, n in left_out.items() if n > 0},
        )

    def _host(self, host: HostView) -> HostLine:
        return HostLine(
            id=host.id,
            name=_line(host.name, NAME_CHARS),
            health=host.health,
            reachable=host.reachable,
            last_seen=self._at(host.last_seen),
            quiet=self._quiet(host.quiet, "host"),
        )

    def _service(self, service: ServiceView, names: dict[str, str]) -> ServiceLine:
        return ServiceLine(
            id=_line(service.id, NAME_CHARS),
            name=_line(service.name, NAME_CHARS),
            host=service.host_id,
            health=service.health,
            containers=[
                ContainerLine(
                    name=_line(c.name, NAME_CHARS),
                    state=c.state,
                    health=c.health,
                    exit_code=c.exit_code,
                    restarts=c.restart_count,
                )
                for c in service.containers[:CONTAINERS_KEPT]
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
            title=_line(incident.title, TITLE_CHARS),
            subject=_line(incident.subject.service_id or incident.subject.host_id, NAME_CHARS),
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
                SignalLine(
                    kind=s.kind,
                    container=_line(s.container, NAME_CHARS) if s.container else None,
                    at=self._at(s.at) or "",
                )
                for s in signals[:SIGNALS_KEPT]
            ]
            found = checks(events)
            facts.checks = found[-CHECKS_KEPT:]
            if len(found) > CHECKS_KEPT:
                facts.left_out["checks"] = len(found) - CHECKS_KEPT
            if waiting and record is not None:
                shown = redacted_proposal(record.proposal)
                facts.plan = [
                    StepLine(action=s.action, target=_line(s.target, NAME_CHARS), host=s.host_id)
                    for s in shown.steps
                ]
            facts.notes = notes[-NOTES_KEPT:]
            summary = _text(report, "summary")
            facts.summary = _line(summary, CAUSE_CHARS * 2) if summary else None
        return facts


def checks(events: list[IncidentEvent]) -> list[CheckLine]:
    """What the run looked at, in order: each tool call with the first line of its result, and
    each of Verify's checks. A result goes to the call with its id; an older result without one
    only to the call just before it, when that is a call of its tool still without a result."""
    found: list[CheckLine] = []
    by_id: dict[str, CheckLine] = {}
    last: tuple[str, CheckLine] | None = None
    for event in events:
        payload = event.payload
        name = str(payload.get("name") or "?")
        if event.kind == "tool_call":
            args = payload.get("args")
            check = _call(name, args if isinstance(args, dict) else {})
            found.append(check)
            if isinstance(call_id := payload.get("id"), str):
                by_id[call_id] = check
            last = (name, check)
        elif event.kind == "tool_result":
            call_id = payload.get("id")
            if isinstance(call_id, str):
                check = by_id.pop(call_id, None)
            else:
                check = last[1] if last is not None and last[0] == name else None
            last = None
            if check is not None:
                check.result = _said(name, payload.get("summary_first_line"))
        elif event.kind == "verification":
            found += [_verified(c) for c in payload.get("checks") or [] if isinstance(c, dict)]
    return found


def _said(tool: str, line: object) -> str | None:
    """A result's line, or None when it says nothing about what was found: a note of the guard's
    (`[… N chars cut]` from a cut head, `[… repeated N times]`) or the head a tool always starts
    with (`list_containers`' column names, `inspect_container`'s `name:` line); and always for
    a host command (`_NO_RESULT`)."""
    if tool in _NO_RESULT or not isinstance(line, str) or not line.strip():
        return None
    one = " ".join(line.split())
    head = _TOOL_HEADS.get(tool)
    if _GUARD_NOTE.fullmatch(one) or (head is not None and head.fullmatch(one)):
        return None
    return _line(one, RESULT_CHARS)


def _call(name: str, args: dict[str, Any]) -> CheckLine:
    target = next((args[k] for k in _TARGET_ARGS if _filled(args.get(k))), None)
    minutes = next((args[k] for k in _MINUTES_ARGS if _whole(args.get(k))), None)
    return CheckLine(
        tool=_line(name, NAME_CHARS),
        target=_line(target, TARGET_CHARS) if target else None,
        minutes=minutes,
    )


def _whole(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int)


def _verified(check: dict[str, Any]) -> CheckLine:
    """One of Verify's checks: passed or failed, and its own short line
    (`schemas.graph.Check.detail`), which may name a container; the target is a container name or
    a URL from the hosts file. Both are guarded with the rest of the incident."""
    target, detail = check.get("target"), check.get("detail")
    said = "passed" if check.get("ok") else "failed"
    if _filled(detail):
        said = f"{said}: {detail}"
    return CheckLine(
        tool=f"verify_{check.get('kind') or 'check'}",
        target=_line(target, TARGET_CHARS) if _filled(target) else None,
        result=_line(said, RESULT_CHARS),
    )


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _last(events: list[IncidentEvent], kind: str) -> dict[str, Any] | None:
    found = [e.payload for e in events if e.kind == kind]
    return found[-1] if found else None


def _text(payload: dict[str, Any] | None, key: str) -> str | None:
    value = (payload or {}).get(key)
    return value if isinstance(value, str) and value.strip() else None
