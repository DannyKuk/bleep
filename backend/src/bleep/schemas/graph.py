"""The shapes the incident graph keeps in its state and hands between nodes.

Every Milestone 2 node's model is defined here at once, so the node tasks can be built side by
side against fixed shapes. Milestone 3 filled in `StepResult` (#24), `VerificationResult` and
`Check` (#26) and `Report` (#27). See docs/design/graph.md.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from bleep.schemas.docker import ContainerSummary, HostBasics

Outcome = Literal[
    "resolved", "rejected", "no_action", "unverified", "budget", "unsure", "replan_limit"
]
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


class ServiceFact(BaseModel):
    """Something worth knowing about a service, kept in long-term memory (Bleep's own, trusted).

    `source` is `human` for a fact a person added, `report` for one taken from a report.
    """

    id: int
    text: str
    source: Literal["report", "human"]
    created_at: datetime


class GatherContext(BaseModel):
    """What Gather collected before any model ran.

    `logs` maps a container name to its last log lines, already redacted, cleaned, capped and
    wrapped as untrusted data (`guard.pipeline.guard_text`), so it can go into a prompt as is.
    Everything else is Bleep's own data. `similar` and `facts` are Bleep's memory: trusted, except
    quoted text in them, which `memory.text` wraps as untrusted when it goes into a prompt.
    `gaps` names what could not be read, one short line each.
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
    facts: list[ServiceFact] = Field(default_factory=list)
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
    """A target container as it was when approval was asked; a change means a stale plan.

    `host_id` and `target` name it as the plan does; `container_id` is what the name resolved to.
    """

    host_id: str
    target: str
    container_id: str
    created: datetime | None = None
    started_at: datetime | None = None


class ApprovalRequest(BaseModel):
    id: str
    plan_hash: str


class ApprovalRecord(BaseModel):
    """An approval as stored: the plan put to the human, its targets then, and how it ended."""

    id: str
    incident_id: str
    plan_hash: str
    proposal: Proposal
    snapshots: list[TargetSnapshot] = Field(default_factory=list)
    requested_at: datetime
    decision: Decision | None = None  # None while pending
    reminders: int = 0

    @property
    def request(self) -> ApprovalRequest:
        return ApprovalRequest(id=self.id, plan_hash=self.plan_hash)


class ApprovalGrant(BaseModel):
    """Proof that a human approved this exact plan; act tools refuse to run without it."""

    incident_id: str
    plan_hash: str
    token: str


class StepResult(BaseModel):
    """What one step of an approved plan did, in Bleep's own words (never host text).

    `step_index` is the step's 0-based place in the plan. `started_at` is None for a step that
    never ran (skipped), `finished_at` for one that has not finished.
    """

    step_index: int
    action: Action
    target: str
    ok: bool
    detail: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


StepStatus = Literal["started", "done", "failed", "skipped"]


class LoggedStep(BaseModel):
    """One row of the action log: a step's status in its plan, and its result so far."""

    plan_hash: str
    host_id: str
    status: StepStatus
    result: StepResult


class Check(BaseModel):
    """One thing Verify looked at: a container's state or a URL's answer."""

    kind: Literal["container", "url"]
    target: str  # the container name, or the URL
    ok: bool
    detail: str = ""  # one short line from Bleep's own checks, never host or reply text


class VerificationResult(BaseModel):
    """What Verify found at one check: `ok` only when every planned check passed.

    `attempt` counts the checks of this verification round, from 1.
    """

    ok: bool
    checks: list[Check] = Field(default_factory=list)
    attempt: int = 1

    @property
    def detail(self) -> str:
        """The checks as short lines, for a chat message or the live view."""
        if not self.checks:
            return "Nothing could be checked."
        return "\n".join(
            f"{'ok' if c.ok else 'FAILED'} {c.kind} {c.target}: {c.detail}" for c in self.checks
        )


class TimelineEntry(BaseModel):
    """One moment of an incident, written by Bleep's code from its stored events."""

    at: datetime
    text: str


class Report(BaseModel):
    """The incident write-up: what happened, why, what was done and whether it worked.

    The model writes `summary`, `cause` and `follow_ups`, checked by `guard.report_check` before
    they are stored or sent; everything else is Bleep's own. `outcome` is the fixed text for the
    run's outcome ("Fixed and verified" only for `resolved`). `action_taken` is None when nothing
    ran.
    """

    title: str
    summary: str
    cause: str
    action_taken: str | None = None
    outcome: str
    timeline: list[TimelineEntry] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    human_decision: str | None = None
