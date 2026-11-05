"""Incidents, the signals that open them, and the events they publish."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SignalKind = Literal[
    "container_died",
    "oom",
    "unhealthy",
    "restart_loop",
    "down_past_grace",
    "url_failed",
    "disk",
    "memory",
    "load",
    "host_unreachable",
]
IncidentStatus = Literal[
    "open",
    "investigating",
    "awaiting_approval",
    "executing",
    "verifying",
    "paused",
    "waiting",  # a host-unreachable incident waiting for its host to answer again
    "resolved",
    "closed",
    "failed",
]
# Statuses after which an incident no longer absorbs signals.
TERMINAL_STATUSES: tuple[str, ...] = ("resolved", "closed", "failed")

# Kinds the graph publishes on the bus (free-form on the wire, shared by name).
EventKind = Literal[
    "node_started",
    "node_finished",
    "tool_call",
    "tool_result",
    "diagnosis",
    "proposal",
    "approval_requested",
    "decision",
    "action",
    "verification",
    "report",
    "notice",
    "expected",  # "This is expected": what was recorded, and on which channel
]


class Subject(BaseModel):
    kind: Literal["service", "host"]
    host_id: str
    service_id: str | None = None

    @property
    def key(self) -> str:
        """Stable identity used to allow one open incident per subject."""
        return f"{self.kind}:{self.host_id}:{self.service_id or ''}"


class Signal(BaseModel):
    subject: Subject
    kind: SignalKind
    at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)
    container: str | None = None


class Incident(BaseModel):
    id: str
    subject: Subject
    status: IncidentStatus
    opened_at: datetime
    closed_at: datetime | None = None
    title: str
    cost_usd: float | None = None
    tokens: int = 0
    trace_url: str | None = None  # set when tracing is on (docs/design/observability.md)
    # The prompt overrides in force when its run started, name to a hash of the text
    # (`prompts.text_version`); None before the run started (docs/design/dev-panel.md).
    prompt_overrides: dict[str, str] | None = None
    # The first incident of its group when this one repeats it (docs/design/watcher.md, folding).
    fold_of: str | None = None


class IncidentEvent(BaseModel):
    incident_id: str
    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime


class RecoveryEvent(BaseModel):
    id: int | None = None
    host_id: str
    service_id: str | None = None
    container: str
    at: datetime
    explanation: str
