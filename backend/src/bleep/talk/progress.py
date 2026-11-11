"""How a look the dashboard's panel started goes (#458), read from Bleep's own state.

The panel has no thread for a look's end message, so it asks here every few seconds. While the
run goes on, one fixed line says what it does now, picked from the last node it started or tool
it called (`talk/look_progress/`); tool arguments and host text never reach it. A run paused
for the model or waiting for its host says so instead, from the incident's status. While its plan
waits for a yes, and once the incident ended, the line sums it up: a fixed headline for the
outcome, as Telegram's end message picks it (`notify/notifier._end_template`), with the report's
summary, or the diagnosis' cause while a plan waits, as one redacted, capped line
(`talk/look_end/`). "What I did" lists the run's steps in fixed words (`talk/look_step/`), each
kind once, at its first place; a container is named only when it is one of the service's
containers in the registry. The summary an ended look gets is kept on the press in the talk
log, once.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from bleep import prompts
from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.guard.titles import split_service_id, title_name
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.shown import report_in
from bleep.schemas.graph import Diagnosis, Report, outcome_of
from bleep.schemas.incidents import TERMINAL_STATUSES, Incident, IncidentEvent
from bleep.schemas.services import Service
from bleep.schemas.talk import LookProgress, TalkLink
from bleep.talk.links import links_in
from bleep.talk.log import TalkLog
from bleep.talk.state import Plans
from bleep.talk.times import duration

SUMMARY_CHARS = 300  # the model's words in a summary, after the fixed headline
_NODES = {
    "gather": "start",
    "investigate": "start",
    "diagnose": "diagnose",
    "propose": "propose",
    "approval": "propose",
    "execute": "execute",
    "verify": "verify",
    "report": "report",
}
_TOOLS = {
    "list_containers": "containers",
    "container_logs": "logs",
    "inspect_container": "inspect",
    "container_stats": "stats",
    "recent_events": "events",
    "host_command": "host",
    "knowledge_search": "docs",
    "recall_incidents": "past",
    "recent_commits": "repo",
    "commit_diff": "repo",
}
_TOOL_PREFIXES = (("coolify_", "coolify"), ("mcp_", "mcp"))
_ACTS = {
    "docker.restart": "restart",
    "docker.stop": "stop",
    "docker.start": "start",
    "coolify.redeploy": "redeploy",
    "coolify.set_env": "set_env",
    "mcp.call": "mcp_call",
}
_CHECKS = {"url": "check_url", "container": "check_containers", "host": "host"}
# The steps that name the containers they were about, when every one is the service's.
_NAMED = frozenset({"logs", "inspect", "stats", "restart", "stop", "start"})
_END_OF = {
    "resolved": "fixed",
    "healthy_anyway": "healthy_again",
    "healthy": "healthy",
    "unverified": "unverified",
    "rejected": "rejected",
    "no_action": "no_action",
}


# A run that waits for something but a person: the model to answer again (`paused`, retried by
# the runner) or the incident's host (`waiting`). Its last event would say it still works.
_HELD = {"paused": "paused", "waiting": "host_waiting"}


class RunState(Protocol):
    """The part of `IncidentRunner` that reads a run's checkpoint: its diagnosis."""

    async def state(self, incident_id: str) -> dict[str, Any]: ...


class Services(Protocol):
    """The part of `hosts.registry.Registry` that knows a service's containers."""

    def service(self, service_id: str) -> Service | None: ...


def _english() -> str:
    return "en"


@dataclass
class LookFollower:
    incidents: IncidentRepo
    bus: EventBus
    plans: Plans
    runs: RunState
    services: Services
    log: TalkLog
    clock: Callable[[], datetime] = utcnow
    language: Callable[[], str] = _english

    async def look(self, incident_id: str) -> LookProgress | None:
        """How the incident's run goes now; None for an unknown incident."""
        incident = await self.incidents.get(incident_id)
        if incident is None:
            return None
        events, language = await self.bus.history(incident_id), self.language()
        steps = steps_of(events, self._containers(incident), language)
        if incident.status in TERMINAL_STATUSES:
            line = await self._ended(incident, events, language)
            self.log.looked("panel", incident.id, line)
            return LookProgress(state="ended", line=line, links=_links(incident, line), steps=steps)
        if incident.status == "awaiting_approval" and (
            line := await self._waits(incident, language)
        ):
            return LookProgress(
                state="waiting", line=line, links=_links(incident, line), steps=steps
            )
        if incident.status in _HELD:
            line = _held(incident, language)
            return LookProgress(state="waiting", line=line, steps=steps)
        return LookProgress(state="running", line=progress_line(events, language), steps=steps)

    def _containers(self, incident: Incident) -> set[str]:
        service_id = incident.subject.service_id
        service = self.services.service(service_id) if service_id else None
        return {c.name for c in service.containers} if service is not None else set()

    async def _waits(self, incident: Incident, language: str) -> str | None:
        """The summary of a plan that waits for a yes; None when none does (yet)."""
        record = await self.plans.current(incident.id)
        if record is None or record.decision is not None or record.closed:
            return None
        cause = _cause((await self.runs.state(incident.id)).get("diagnosis"))
        cause = cause or record.proposal.reason
        name = "waits_refused" if record.proposal.refused else "waits"
        return _end(name, language, incident=_name(incident), cause=one_line(cause))

    async def _ended(self, incident: Incident, events: list[IncidentEvent], language: str) -> str:
        report = _report(events)
        if report is None:
            name = "failed" if incident.status == "failed" else "closed"
            return _end(name, language, incident=_name(incident))
        kind = outcome_of(report.outcome)
        name = _END_OF.get(kind or "", "stopped")
        if name == "no_action" and await self._let_go(incident.id):
            name = "rejected"
        if name == "healthy" and not _url_checked(events):
            name = "healthy_no_url"
        # The outcome above is read from the stored English; only the summary is shown.
        summary = report_in(report, language).summary
        values = {"incident": _name(incident), "summary": one_line(summary)}
        if name in ("healthy", "healthy_no_url", "healthy_again", "unverified"):
            values["subject"] = _service(incident)
        if name == "fixed":
            values["took"] = duration(
                (incident.closed_at or self.clock()) - incident.opened_at, language
            )
        return _end(name, language, **values)

    async def _let_go(self, incident_id: str) -> bool:
        """Whether the person let a plan Bleep's own checks refused go, which Telegram's end
        message words as a rejected plan too."""
        current = await self.plans.current(incident_id)
        return (
            current is not None
            and current.proposal.refused
            and current.decision is not None
            and current.decision.kind == "reject"
        )


def progress_line(events: list[IncidentEvent], language: str = "en") -> str:
    """What the run does now: the last node it started or tool it called, in fixed words."""
    name = "start"
    for event in events:
        if event.kind == "node_started":
            name = _NODES.get(str(event.payload.get("node")), "start")
        elif event.kind == "tool_call":
            name = _tool(event.payload.get("name"))
    return prompts.load_in(language, f"talk/look_progress/{name}").strip()


def steps_of(events: list[IncidentEvent], containers: set[str], language: str = "en") -> list[str]:
    """What the run did, in order, each kind of step once at its first place; a step names its
    containers only when each it was about is one of `containers`."""
    order: list[str] = []
    named: dict[str, list[str]] = {}
    vague: set[str] = set()

    def did(step: str, target: object = None) -> None:
        if step not in named:
            order.append(step)
            named[step] = []
        if step not in _NAMED:
            return
        if isinstance(target, str) and target in containers:
            if target not in named[step]:
                named[step].append(target)
        else:
            vague.add(step)

    for event in events:
        payload = event.payload
        if event.kind == "tool_call":
            args = payload.get("args")
            container = args.get("container") if isinstance(args, dict) else None
            did(_tool(payload.get("name")), container)
        elif event.kind == "verification":
            for check in payload.get("checks") or []:
                if isinstance(check, dict) and (step := _CHECKS.get(str(check.get("kind")))):
                    did(step)
        elif event.kind == "action" and payload.get("status") == "done":
            did(_ACTS.get(str(payload.get("action")), "act_other"), payload.get("target"))
        elif event.kind == "action" and payload.get("status") == "failed":
            did("act_failed")
    return [_step(step, named[step], step in vague, language) for step in order]


def one_line(text: str, limit: int = SUMMARY_CHARS) -> str:
    """Model text as one redacted line for the panel: control and invisible characters dropped,
    secrets redacted, every run of blanks and line breaks one space, cut to `limit`."""
    one = " ".join(redact_text(strip_control(text)).split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _step(step: str, containers: list[str], vague: bool, language: str) -> str:
    if step in _NAMED and containers and not vague:
        name = f"talk/look_step/{step}_of"
        return prompts.load_in(language, name, containers=", ".join(containers)).strip()
    return prompts.load_in(language, f"talk/look_step/{step}").strip()


def _held(incident: Incident, language: str) -> str:
    """Why the run waits, the host named by its id from the hosts file."""
    name = f"talk/look_progress/{_HELD[incident.status]}"
    if incident.status == "waiting":
        return prompts.load_in(language, name, host=incident.subject.host_id).strip()
    return prompts.load_in(language, name).strip()


def _tool(name: object) -> str:
    if not isinstance(name, str):
        return "tool"
    if name in _TOOLS:
        return _TOOLS[name]
    return next((group for prefix, group in _TOOL_PREFIXES if name.startswith(prefix)), "tool")


def _end(name: str, language: str, /, **values: str) -> str:
    return prompts.load_in(language, f"talk/look_end/{name}", **values).strip()


def _name(incident: Incident) -> str:
    return incident.short_name or incident.id


def _service(incident: Incident) -> str:
    """The service as the incident's short name names it ("shop #7" names shop), as Telegram's
    end message does, or by its key through `title_name` when it has none."""
    named = (incident.short_name or "").rsplit(" #", 1)[0]
    return named or title_name(split_service_id(incident.subject)[1])


def _links(incident: Incident, line: str) -> list[TalkLink]:
    return links_in(line, [TalkLink(kind="incident", id=incident.id, label=_name(incident))])


def _cause(diagnosis: object) -> str | None:
    """The diagnosis' root cause, as the checkpoint holds it: the model, or its fields."""
    if isinstance(diagnosis, Diagnosis):
        return diagnosis.root_cause
    if isinstance(diagnosis, dict) and isinstance(cause := diagnosis.get("root_cause"), str):
        return cause
    return None


def _report(events: list[IncidentEvent]) -> Report | None:
    found = [e.payload for e in events if e.kind == "report"]
    if not found:
        return None
    try:
        return Report.model_validate(found[-1])
    except ValidationError:
        return None


def _url_checked(events: list[IncidentEvent]) -> bool:
    """Whether the run's last verification checked a URL, as Telegram's end message asks."""
    checks = [e for e in events if e.kind == "verification"]
    return bool(checks) and any(
        isinstance(c, dict) and c.get("kind") == "url" for c in checks[-1].payload.get("checks", [])
    )
