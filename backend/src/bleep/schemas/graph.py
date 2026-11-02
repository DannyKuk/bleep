"""The shapes the incident graph keeps in its state and hands between nodes.

Every Milestone 2 node's model is defined here at once, so the node tasks can be built side by
side against fixed shapes. `StepResult`, `VerificationResult` and `Report` are minimal
placeholders that Milestone 3 (Execute, Verify, Report) fills out. See docs/design/graph.md.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from bleep.schemas.docker import ContainerSummary, HostBasics

Outcome = Literal["resolved", "rejected", "no_action", "unverified", "budget", "unsure"]
Action = Literal[
    "docker.restart", "docker.stop", "docker.start", "coolify.redeploy", "coolify.set_env"
]


class ContainerMemory(BaseModel):
    """One container's memory use, for a host incident's "who is using it" list."""

    name: str
    mem_usage: int  # bytes
    mem_limit: int  # bytes; 0 when the engine reports none
    mem_percent: float


class PastIncident(BaseModel):
    """A similar earlier incident recalled from long-term memory (Bleep's own, trusted)."""

    incident_id: str
    date: datetime
    title: str
    cause: str
    action: str
    outcome: str
    human_decision: str | None = None
    feedback: str | None = None
    score: float


class GatherContext(BaseModel):
    """What Gather collected before any model ran.

    `logs` maps a container name to its last log lines, already redacted, cleaned, capped and
    wrapped as untrusted data (`guard.pipeline.guard_text`), so it can go into a prompt as is.
    Everything else is Bleep's own data. `gaps` names what could not be read, one short line each.
    """

    at: datetime
    service_name: str | None = None
    containers: list[ContainerSummary] = Field(default_factory=list)
    logs: dict[str, str] = Field(default_factory=dict)
    basics: HostBasics | None = None
    top_memory: list[ContainerMemory] = Field(default_factory=list)
    host_rules: list[str] = Field(default_factory=list)
    service_rules: list[str] = Field(default_factory=list)
    similar: list[PastIncident] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    source: str  # a tool name, "gather", "memory", ...
    detail: str  # one short statement, never a raw log dump


class Diagnosis(BaseModel):
    root_cause: str
    category: Literal["config", "resource", "dependency", "code", "network", "host", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    affected_services: list[str] = Field(default_factory=list)
    unsure: bool = False


class PlanStep(BaseModel):
    action: Action
    host_id: str
    target: str  # container name, or the Coolify application for coolify.* actions
    params: dict[str, str] = Field(default_factory=dict)


class VerificationPlan(BaseModel):
    containers: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    """A plan approved as a whole: no steps for `no_action`, one to three for `act`.

    The step count is checked by `check_plan` (a violation goes back to the model), not by this
    model, so a model's over-long plan reaches the check instead of failing to parse.
    """

    kind: Literal["act", "no_action"]
    steps: list[PlanStep] = Field(default_factory=list)
    reason: str
    verification: VerificationPlan = Field(default_factory=VerificationPlan)
    human_instructions: str | None = None


class Decision(BaseModel):
    kind: Literal["approve", "reject", "instruct"]
    text: str | None = None  # the instruction, for `instruct`
    via: Literal["telegram", "dashboard"]
    at: datetime


class TargetSnapshot(BaseModel):
    """A target container as it was when approval was asked; a change means a stale plan."""

    container_id: str
    created: datetime | None = None
    started_at: datetime | None = None


class ApprovalRequest(BaseModel):
    id: str
    plan_hash: str


class ApprovalGrant(BaseModel):
    """Proof that a human approved this exact plan; act tools refuse to run without it."""

    incident_id: str
    plan_hash: str
    token: str


class StepResult(BaseModel):
    """Placeholder until Milestone 3's Execute: what one plan step did."""

    step: PlanStep
    ok: bool
    detail: str = ""


class VerificationResult(BaseModel):
    """Placeholder until Milestone 3's Verify: whether the service is healthy again."""

    ok: bool
    detail: str = ""


class Report(BaseModel):
    """Placeholder until Milestone 3's Report: the incident write-up."""

    summary: str
