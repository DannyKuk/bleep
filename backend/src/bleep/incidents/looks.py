"""A look on request: the person asks Bleep to look at a service now, and only their confirmed
tap opens an incident for it (docs/design/incidents.md, #403).

The incident opens as the watcher's do, through `IncidentRepo.open_or_attach` and then
`IncidentRunner.start`, with one `look_requested` signal: one open incident per subject holds,
and the run gathers, investigates and proposes as for any incident, its plan waiting for a yes.
Nothing opens when the service has an open incident (or its host has one that takes its
signals), is quiet, is unknown or Bleep's own, its host does not answer, or the day's cost cap
is spent; the `Look` says which, and the surface words it (`talk.looks.look_said`).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol

from bleep.guard.titles import build_title
from bleep.hosts.discovery import is_self
from bleep.hosts.registry import Registry
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.llm.usage import CostLedger
from bleep.schemas.incidents import LOOK_REQUESTED, Incident, Signal, Subject
from bleep.schemas.quiet import QuietState
from bleep.schemas.services import Service
from bleep.schemas.settings import Budgets

Channel = Literal["telegram", "dashboard"]
LookOutcome = Literal["opened", "open", "quiet", "unknown", "unreachable", "spent"]


class Starter(Protocol):
    """The part of `IncidentRunner` a look uses."""

    async def start(self, incident: Incident) -> Any: ...


@dataclass(frozen=True)
class Look:
    """What came of a look: `incident` is the one it opened, or the one already open that
    covers the service; `quiet` the quiet that keeps it from opening one."""

    outcome: LookOutcome
    service_id: str
    name: str = ""  # the service's name from the registry, as the dashboard shows it
    host_id: str = ""
    incident: Incident | None = None
    quiet: QuietState | None = None


class Looks:
    """`budgets` gives the day's cost cap in force, `ledger` what was spent today."""

    def __init__(
        self,
        repo: IncidentRepo,
        runner: Starter,
        quiet: QuietStore,
        registry: Registry,
        *,
        ledger: CostLedger | None = None,
        budgets: Callable[[], Budgets] = Budgets,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.repo = repo
        self.runner = runner
        self.quiet = quiet
        self.registry = registry
        self.ledger = ledger
        self.budgets = budgets
        self.clock = clock

    async def look(self, service_id: str, via: Channel) -> Look:
        """Open an incident on request for the service `service_id` and start its run, or say
        why not. Checked again at the tap, whatever the offer saw: the state may have moved."""
        service = self.registry.service(service_id)
        if service is None or any(is_self(c.labels) for c in service.containers):
            return Look("unknown", service_id)
        seen = Look("unknown", service.id, service.name, service.host_id)
        subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
        if (open_one := await self.repo.open_target(subject)) is not None:
            return replace(seen, outcome="open", incident=open_one)
        if (quiet := await self.quiet.for_subject(subject)) is not None:
            return replace(seen, outcome="quiet", quiet=quiet)
        if not self._reachable(service.host_id):
            return replace(seen, outcome="unreachable")
        if await self._spent():
            return replace(seen, outcome="spent")
        signal = Signal(subject=subject, kind=LOOK_REQUESTED, at=self.clock(), detail={"via": via})
        incident, created = await self.repo.open_or_attach(
            subject,
            [signal],
            name=service.name,
            attach=False,
            title=self._title(service, subject, signal),
        )
        if not created:  # the watcher opened one in the meantime
            return replace(seen, outcome="open", incident=incident)
        await self.runner.start(incident)
        return replace(seen, outcome="opened", incident=incident)

    def _title(self, service: Service, subject: Subject, signal: Signal) -> str:
        """The look's title, naming the service as the dashboard does (#421): "look requested on
        dkwebsite", not by a Coolify app's uuid. A name another host's service has too gets its
        host ("… on shop on laptop"); one another service on the same host has too keeps the
        service id, whose kind tells the two apart ("… on a/compose:shop")."""
        alike = [
            s
            for s in self.registry.services()
            if s.id != service.id and s.name.casefold() == service.name.casefold()
        ]
        if any(s.host_id == service.host_id for s in alike):
            return build_title(subject, [signal])
        return build_title(subject, [signal], service.name, on_host=bool(alike))

    def _reachable(self, host_id: str) -> bool:
        host = next((h for h in self.registry.hosts() if h.config.id == host_id), None)
        return host is not None and host.reachable

    async def _spent(self) -> bool:
        """Whether the day's cost cap, the one incident runs count against, is spent."""
        if self.ledger is None:
            return False
        return await self.ledger.spent_today() >= self.budgets().daily_cost_usd
