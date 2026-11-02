"""What the graph's nodes run on, passed in once when the graph is built.

`Memory`, `Notifier`, `Approvals` and `SettingsSource` are Protocols with small defaults, so the
graph runs (and is tested) before long-term memory, Telegram, the approvals table and the
settings store exist; each later piece is passed in their place.
"""

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from langchain_core.language_models import BaseChatModel

from bleep.guard.log import GuardLogger
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import utcnow
from bleep.llm.factory import Role, chat_model
from bleep.llm.usage import CostLedger
from bleep.schemas.graph import (
    ApprovalRequest,
    Decision,
    Diagnosis,
    PastIncident,
    Proposal,
    Report,
    TargetSnapshot,
    VerificationResult,
)
from bleep.schemas.incidents import Incident
from bleep.schemas.settings import Budgets, ModelSettings
from bleep.tools.registry import ToolRegistry

# The actions Bleep can take without a plugin; plugins add theirs (coolify.*) when enabled.
CORE_ACTIONS: frozenset[str] = frozenset({"docker.restart", "docker.stop", "docker.start"})

ModelFactory = Callable[[Role], BaseChatModel]


class Memory(Protocol):
    """Long-term memory, as Gather reads it."""

    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3
    ) -> list[PastIncident]: ...


class NullMemory:
    """No memory yet: nothing is ever similar."""

    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3
    ) -> list[PastIncident]:
        return []


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
    """Pending approvals, decided once: the first decision wins, from any channel."""

    async def request(
        self, incident_id: str, proposal: Proposal, snapshots: list[TargetSnapshot]
    ) -> ApprovalRequest: ...

    async def resolve(self, incident_id: str, decision: Decision) -> Decision | None:
        """The decision if it won, None if the approval was already decided or never asked."""
        ...


def plan_hash(proposal: Proposal) -> str:
    """sha256 of the steps as canonical JSON."""
    steps = [s.model_dump(mode="json") for s in proposal.steps]
    return hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest()


class InMemoryApprovals:
    """Approvals in a dict: lost on restart, so only for tests and until the SQLite one (#20)."""

    def __init__(self) -> None:
        self.pending: dict[str, ApprovalRequest] = {}

    async def request(
        self, incident_id: str, proposal: Proposal, snapshots: list[TargetSnapshot]
    ) -> ApprovalRequest:
        request = ApprovalRequest(id=secrets.token_hex(8), plan_hash=plan_hash(proposal))
        self.pending[incident_id] = request
        return request

    async def resolve(self, incident_id: str, decision: Decision) -> Decision | None:
        return decision if self.pending.pop(incident_id, None) is not None else None


class SettingsSource(Protocol):
    """The settings a node reads at the moment it runs, so a change applies to the next step."""

    def budgets(self) -> Budgets: ...

    def models(self) -> ModelSettings: ...

    def enabled_actions(self) -> set[str]: ...


@dataclass
class StaticSettings:
    """Fixed settings: the defaults, or what the environment says. The settings store (#36)
    replaces it with values a human can change from the dashboard."""

    budget_limits: Budgets = field(default_factory=Budgets)
    model_settings: ModelSettings = field(default_factory=ModelSettings)
    actions: frozenset[str] = CORE_ACTIONS

    def budgets(self) -> Budgets:
        return self.budget_limits

    def models(self) -> ModelSettings:
        return self.model_settings

    def enabled_actions(self) -> set[str]:
        return set(self.actions)


def model_factory(settings: SettingsSource) -> ModelFactory:
    """Build each role's chat model from the settings as they are when it is asked for."""
    return lambda role: chat_model(settings.models(), role)


@dataclass(kw_only=True)
class GraphDeps:
    registry: Registry
    tools: ToolRegistry
    models: ModelFactory
    bus: EventBus
    ledger: CostLedger
    memory: Memory = field(default_factory=NullMemory)
    approvals: Approvals = field(default_factory=InMemoryApprovals)
    notifier: Notifier = field(default_factory=NullNotifier)
    guard_log: GuardLogger | None = None
    settings_store: SettingsSource = field(default_factory=StaticSettings)
    clock: Callable[[], datetime] = utcnow
