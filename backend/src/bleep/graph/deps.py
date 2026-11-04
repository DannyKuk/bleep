"""What the graph's nodes run on, passed in once when the graph is built.

`Memory`, `Notifier`, `Approvals` and `SettingsSource` are Protocols with small defaults, so the
graph runs (and is tested) without long-term memory, Telegram or the settings store; the app
passes its own in their place (`memory.store.MemoryStore`, `incidents.approvals.ApprovalStore`).
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from bleep.guard.grant import grant_token, plan_hash
from bleep.guard.log import GuardLogger
from bleep.hosts.registry import Registry
from bleep.incidents.action_log import ActionLog
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.llm.factory import Role, chat_model
from bleep.llm.guard import ModelGuard
from bleep.llm.usage import CostLedger
from bleep.schemas.graph import (
    ApprovalGrant,
    ApprovalRecord,
    ApprovalRequest,
    Decision,
    Diagnosis,
    PastIncident,
    Proposal,
    Report,
    ServiceFact,
    TargetSnapshot,
    VerificationResult,
)
from bleep.schemas.incidents import Incident
from bleep.schemas.settings import Budgets, ModelSettings, Thresholds
from bleep.tools.registry import ActHandler, ToolRegistry

# The actions Bleep can take without a plugin; plugins add theirs (coolify.*) when enabled.
CORE_ACTIONS: frozenset[str] = frozenset({"docker.restart", "docker.stop", "docker.start"})

ModelFactory = Callable[[Role], BaseChatModel]


class Memory(Protocol):
    """Long-term memory: Gather and the `recall_incidents` tool read it, Report writes to it
    (the record and the facts the human's instructions taught).

    `memory.store.MemoryStore` is the SQLite one the app runs on.
    """

    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3, min_score: float = 0.5
    ) -> list[PastIncident]:
        """At most `k` past incidents like `text`, the same service's first."""
        ...

    async def facts(self, service_id: str) -> list[ServiceFact]: ...

    async def add_report(
        self,
        incident: Incident,
        report: Report,
        decision: Decision | None = None,
        *,
        proposal: Proposal | None = None,
    ) -> None:
        """Remember a finished incident with the human's decision and feedback on it."""
        ...

    async def replace_report_facts(
        self, service_id: str, incident_id: str, texts: Iterable[str]
    ) -> list[ServiceFact]:
        """Keep the facts the incident's report taught, in place of earlier ones from it."""
        ...


class NullMemory:
    """No memory: nothing is ever similar, no service has facts, and nothing is kept."""

    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3, min_score: float = 0.5
    ) -> list[PastIncident]:
        return []

    async def facts(self, service_id: str) -> list[ServiceFact]:
        return []

    async def add_report(
        self,
        incident: Incident,
        report: Report,
        decision: Decision | None = None,
        *,
        proposal: Proposal | None = None,
    ) -> None:
        return None

    async def replace_report_facts(
        self, service_id: str, incident_id: str, texts: Iterable[str]
    ) -> list[ServiceFact]:
        return []


class Tracer(Protocol):
    """Traces incident runs (`bleep.llm.tracing`); the runner asks it for each graph run."""

    def run_config(self, incident: Incident) -> RunnableConfig:
        """What a graph run of `incident` adds to its config; empty when tracing is off."""
        ...

    def trace_url(self, incident: Incident) -> str | None:
        """The link to the incident's trace, known before the trace exists; None if none."""
        ...

    async def close(self) -> None:
        """Send what is still buffered."""
        ...


class Notifier(Protocol):
    """Tells the human, on every configured channel, what the graph needs them to know."""

    async def incident_opened(self, incident: Incident) -> None: ...

    async def proposal(
        self,
        incident: Incident,
        diagnosis: Diagnosis | None,
        proposal: Proposal,
        approval: ApprovalRequest,
    ) -> None: ...

    async def reminder(self, incident: Incident, approval: ApprovalRequest) -> None: ...

    async def verification(self, incident: Incident, result: VerificationResult) -> None: ...

    async def report(self, incident: Incident, report: Report) -> None: ...

    async def notice(self, incident_id: str, text: str) -> None: ...


class NullNotifier:
    """No channel beyond the dashboard, which reads the bus the nodes already publish to."""

    async def incident_opened(self, incident: Incident) -> None:
        return None

    async def proposal(
        self,
        incident: Incident,
        diagnosis: Diagnosis | None,
        proposal: Proposal,
        approval: ApprovalRequest,
    ) -> None:
        return None

    async def reminder(self, incident: Incident, approval: ApprovalRequest) -> None:
        return None

    async def verification(self, incident: Incident, result: VerificationResult) -> None:
        return None

    async def report(self, incident: Incident, report: Report) -> None:
        return None

    async def notice(self, incident_id: str, text: str) -> None:
        return None


class Approvals(Protocol):
    """Pending approvals, decided once: the first decision wins, from any channel.

    `incidents.approvals.ApprovalStore` is the SQLite one the app runs on.
    """

    async def request(
        self, incident_id: str, proposal: Proposal, snapshots: list[TargetSnapshot]
    ) -> ApprovalRequest: ...

    async def resolve(
        self, incident_id: str, decision: Decision, approval_id: str | None = None
    ) -> Decision | None:
        """The decision if it won, None if the approval was already decided or never asked;
        with `approval_id`, only that approval can be won."""
        ...

    async def current(self, incident_id: str) -> ApprovalRecord | None:
        """The incident's latest approval, pending or decided."""
        ...

    async def grant(self, incident_id: str) -> ApprovalGrant | None:
        """The grant for the incident's latest plan if, and only if, it was approved."""
        ...


class InMemoryApprovals:
    """Approvals in a dict, lost on restart, so for tests only. Grants are keyed with `secret`
    (random by default); a test that runs act tools passes the one it gives them."""

    def __init__(self, secret: str | None = None) -> None:
        self.records: dict[str, ApprovalRecord] = {}
        self._secret = secret or secrets.token_hex(16)

    async def request(
        self, incident_id: str, proposal: Proposal, snapshots: list[TargetSnapshot]
    ) -> ApprovalRequest:
        record = ApprovalRecord(
            id=secrets.token_hex(8),
            incident_id=incident_id,
            plan_hash=plan_hash(proposal, snapshots),
            proposal=proposal,
            snapshots=snapshots,
            requested_at=utcnow(),
        )
        self.records[incident_id] = record
        return record.request

    async def resolve(
        self, incident_id: str, decision: Decision, approval_id: str | None = None
    ) -> Decision | None:
        record = self.records.get(incident_id)
        if record is None or record.decision is not None:
            return None
        if approval_id is not None and record.id != approval_id:
            return None
        record.decision = decision
        return decision

    async def current(self, incident_id: str) -> ApprovalRecord | None:
        return self.records.get(incident_id)

    async def grant(self, incident_id: str) -> ApprovalGrant | None:
        record = self.records.get(incident_id)
        if record is None or record.decision is None or record.decision.kind != "approve":
            return None
        token = grant_token(self._secret, incident_id, record.plan_hash)
        return ApprovalGrant(incident_id=incident_id, plan_hash=record.plan_hash, token=token)


class SettingsSource(Protocol):
    """The settings a node reads at the moment it runs, so a change applies to the next step."""

    def budgets(self) -> Budgets: ...

    def thresholds(self) -> Thresholds: ...

    def models(self) -> ModelSettings: ...

    def enabled_actions(self) -> set[str]: ...


@dataclass
class StaticSettings:
    """Fixed settings: the defaults, or what the environment says. The settings store (#36)
    replaces it with values a human can change from the dashboard."""

    budget_limits: Budgets = field(default_factory=Budgets)
    threshold_limits: Thresholds = field(default_factory=Thresholds)
    model_settings: ModelSettings = field(default_factory=ModelSettings)
    actions: frozenset[str] = CORE_ACTIONS

    def budgets(self) -> Budgets:
        return self.budget_limits

    def thresholds(self) -> Thresholds:
        return self.threshold_limits

    def models(self) -> ModelSettings:
        return self.model_settings

    def enabled_actions(self) -> set[str]:
        return set(self.actions)


def model_factory(settings: SettingsSource) -> ModelFactory:
    """Build each role's chat model from the settings as they are when it is asked for."""
    return lambda role: chat_model(
        settings.models(), role, timeout_s=settings.budgets().model_timeout_s
    )


@dataclass(kw_only=True)
class GraphDeps:
    """`tools` are the read tools Investigate offers the model; `act_tools` are run by Execute
    alone, keyed by action name, and must be keyed with the secret that keys `approvals`' grants.
    `action_log` is Execute's crash-safety ledger."""

    registry: Registry
    tools: ToolRegistry
    act_tools: Mapping[str, ActHandler]
    models: ModelFactory
    bus: EventBus
    ledger: CostLedger
    incidents: IncidentRepo
    action_log: ActionLog
    memory: Memory = field(default_factory=NullMemory)
    approvals: Approvals = field(default_factory=InMemoryApprovals)
    notifier: Notifier = field(default_factory=NullNotifier)
    guard_log: GuardLogger | None = None
    settings_store: SettingsSource = field(default_factory=StaticSettings)
    clock: Callable[[], datetime] = utcnow
    # Waiting between verification checks; tests pass one that only records the delay.
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # Waiting between retries of a model call; tests pass one that only records the delay.
    model_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # The client Verify checks URLs with; None makes it open one for each check and close it after.
    http: httpx.AsyncClient | None = None

    def guard(self) -> ModelGuard:
        """The bounds of a model call as the settings say now: the next call uses a changed one."""
        return ModelGuard(self.settings_store.budgets().model_timeout_s, sleep=self.model_sleep)
