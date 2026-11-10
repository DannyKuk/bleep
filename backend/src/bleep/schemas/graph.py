"""The shapes the incident graph keeps in its state and hands between nodes.

Every Milestone 2 node's model is defined here at once, so the node tasks can be built side by
side against fixed shapes. Milestone 3 filled in `StepResult` (#24), `VerificationResult` and
`Check` (#26) and `Report` (#27). See docs/design/graph.md.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from bleep.schemas.docker import ContainerSummary, HostBasics
from bleep.schemas.shown import Shown

Outcome = Literal[
    "resolved", "healthy", "rejected", "no_action", "unverified", "budget", "replan_limit"
]
# Where host text gave orders or posed as Bleep (#105): a closed list, so the diagnosis can say
# where without carrying any of that text.
PlantedSource = Literal[
    "logs", "events", "inspect", "http_body", "memory", "commits", "docs", "other"
]
# The places a report can name: Diagnose's list, and the plugins' answers, the host basics and a
# service's name or id, which code alone reads for planted orders.
PlantedPlace = PlantedSource | Literal["mcp", "coolify", "host_basics", "service_name"]
Action = Literal[
    "docker.restart",
    "docker.stop",
    "docker.start",
    "coolify.redeploy",
    "coolify.set_env",
    "mcp.call",
]


class ContainerMemory(BaseModel):
    """One container's memory use, for a host incident's "who is using it" list."""

    name: str
    mem_usage: int  # bytes
    mem_limit: int  # bytes; 0 when the engine reports none
    mem_percent: float


class IncidentRecord(BaseModel):
    """A finished incident as long-term memory keeps it."""

    incident_id: str
    host_id: str
    service_id: str | None = None
    date: datetime
    title: str
    cause: str
    action: str
    outcome: str
    human_decision: str | None = None
    feedback: str | None = None


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


# One limit for each kind of text a person or a report adds to memory, shared by the API, the
# store and Report, so what the dashboard accepts is what memory keeps.
FACT_CHARS = 200  # a service fact
NOTE_CHARS = 200  # a feedback note, inside a past incident's one line


class ServiceFact(BaseModel):
    """Something worth knowing about a service, kept in long-term memory (Bleep's own, trusted).

    `source` is `human` for a fact a person added, `report` for one taken from a report;
    `incident_id` names that report's incident.
    """

    id: int
    text: str
    source: Literal["report", "human"]
    created_at: datetime
    incident_id: str | None = None


FeedbackTarget = Literal["diagnosis", "proposal"]
Vote = Literal["up", "down"]


class Feedback(BaseModel):
    """The human's thumbs up or down on an incident's diagnosis or plan, with an optional note.

    One per target and incident; a later vote replaces it.
    """

    target: FeedbackTarget
    vote: Vote
    note: str | None = None
    at: datetime


class UrlReading(BaseModel):
    """One of the service's URLs as Gather checked it, the watcher's check (`check_url`) run once:
    `error` is Bleep's own text and never quotes the reply."""

    url: str
    ok: bool
    status: int | None = None
    error: str | None = None


class GatherContext(BaseModel):
    """What Gather collected before any model ran.

    `logs` maps a container name to its last log lines, already redacted, cleaned, capped and
    wrapped as untrusted data (`guard.pipeline.guard_text`), so it can go into a prompt as is.
    Everything else is Bleep's own data. `similar` and `facts` are Bleep's memory: trusted, except
    quoted text in them, which `memory.text` wraps as untrusted when it goes into a prompt.
    `gaps` names what could not be read, one short line each. `urls` holds the service's URLs
    checked when the run began, for a look on request only (#423).
    """

    at: datetime
    service_name: str | None = None
    containers: list[ContainerSummary] = Field(default_factory=list)
    logs: dict[str, str] = Field(default_factory=dict)
    urls: list[UrlReading] = Field(default_factory=list)
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
    planted_in: list[PlantedSource] = Field(default_factory=list)


class PlanStep(BaseModel):
    action: Action
    host_id: str
    target: str  # container name (for mcp.call too), or the Coolify application for coolify.*
    params: dict[str, str] = Field(default_factory=dict)


class VerificationPlan(BaseModel):
    containers: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class ProposalDraft(BaseModel):
    """What the model writes: a plan approved as a whole, no steps for `no_action`, one to three
    for `act`. This is the only plan schema the model sees; `Proposal` adds what only code sets.

    The step count is checked by `check_plan` (a violation goes back to the model), not by this
    model, so a model's over-long plan reaches the check instead of failing to parse.
    """

    kind: Literal["act", "no_action"]
    steps: list[PlanStep] = Field(default_factory=list)
    reason: str
    verification: VerificationPlan = Field(default_factory=VerificationPlan)
    human_instructions: str | None = None


class Proposal(ProposalDraft):
    """A plan as the run holds it: the model's draft, and for a refusal what code adds.

    `refused` is a `no_action` made by the plan check refusing the model's plan twice (#113): the
    human is asked to instruct or let it go, and no approval can ever make it run. Only
    `propose._refused` sets `refused`, `refused_steps` and `refusal_reasons`; the model never
    sees them (`ProposalDraft` is its schema) and `propose` builds every plan from a draft.
    """

    refused: bool = False
    refused_steps: list[PlanStep] = Field(default_factory=list)
    refusal_reasons: list[str] = Field(default_factory=list)

    @classmethod
    def from_draft(cls, draft: ProposalDraft) -> Proposal:
        return cls(**draft.model_dump())


class Decision(BaseModel):
    kind: Literal["approve", "reject", "instruct"]
    text: str | None = None  # the instruction, for `instruct`
    via: Literal["telegram", "dashboard"]
    at: datetime
    # `text` as a fixed text of Bleep's ("This is expected."), to show it in the person's
    # language (#427); None for the human's own words.
    shown: Shown | None = None


class TargetSnapshot(BaseModel):
    """A target as it was when approval was asked; a change means a stale plan.

    `host_id` and `target` name it as the plan does. A container target holds `container_id`,
    what the name resolved to; a Coolify application holds `app_uuid` instead (#139), so a
    recreated application with the same name is another target. `restart_count` and `state`
    tell a restart policy's own restarts from a start by hand; they are None in an approval
    stored before #87, and then any new start time is stale.
    """

    host_id: str
    target: str
    container_id: str | None = None
    app_uuid: str | None = None
    created: datetime | None = None
    started_at: datetime | None = None
    restart_count: int | None = None
    state: str | None = None

    @model_validator(mode="after")
    def _one_resolved_id(self) -> TargetSnapshot:
        if (self.container_id is None) == (self.app_uuid is None):
            raise ValueError("a snapshot holds one of container_id and app_uuid")
        return self


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
    decision: Decision | None = None  # None while pending, and when closed
    reminders: int = 0
    closed: bool = False  # its incident finished before anyone answered (#277)

    @property
    def request(self) -> ApprovalRequest:
        return ApprovalRequest(id=self.id, plan_hash=self.plan_hash)


class ApprovalGrant(BaseModel):
    """Proof that a human approved this exact plan; act tools refuse to run without it."""

    incident_id: str
    plan_hash: str
    token: str


REFUSED_DETAIL = "refused, nothing ran"  # how a step the act tool refused at run time begins


class StepResult(BaseModel):
    """What one step of an approved plan did, in Bleep's own words (never host text).

    `step_index` is the step's 0-based place in the plan. `started_at` is None for a step that
    never ran (skipped, or refused at run time), `finished_at` for one that has not finished.
    """

    step_index: int
    action: Action
    target: str
    ok: bool
    detail: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def refused(self) -> bool:
        """The act tool refused the step at run time, so it never reached the engine. Its action
        log row keeps a start time all the same, so this reads the detail, not `started_at`."""
        return not self.ok and self.detail.startswith(REFUSED_DETAIL)

    @property
    def reached_engine(self) -> bool:
        """The step was started and not refused, so it may have changed the host."""
        return self.started_at is not None and not self.refused


StepStatus = Literal["started", "done", "failed", "skipped"]


class LoggedStep(BaseModel):
    """One row of the action log: a step's status in its plan, and its result so far.

    `plan_key` is the plan hash followed by the approval's id, `<hash>:<approval id>`."""

    plan_key: str
    host_id: str
    status: StepStatus
    result: StepResult


# How Verify's failed container check begins when the container was started again after the
# fix (#320); the report's timeline reads it.
STARTED_AFTER_FIX = (
    "restarted by its restart policy after the fix",
    "started again after the fix",
    "recreated after the fix",
)


class Check(BaseModel):
    """One thing Verify looked at: a container's state, a URL's answer, a `host` reading (disk,
    memory or load) against its threshold, or a `symptom` it could not see answered (a stopped
    container nothing shows the service works without, a host problem nothing can read again)."""

    kind: Literal["container", "url", "host", "symptom"]
    target: str  # the container name, the URL, the reading, or the host for a host symptom
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
    """One moment of an incident, written by Bleep's code from its stored events. `text` is
    English; `shown` is what it was made from, to show it in the person's language (#427), None
    for a notice stored before that."""

    at: datetime
    text: str
    shown: Shown | None = None


# The report's fields that hold one of Bleep's fixed texts rather than the model's (#427).
ReportText = Literal["outcome", "cause", "human_decision"]


class Report(BaseModel):
    """The incident write-up: what happened, why, what was done and whether it worked.

    The model writes `summary`, `cause`, `cause_en` and `follow_ups`, checked by
    `guard.report_check` before they are stored or sent; everything else is Bleep's own. `outcome`
    is the fixed text for the run's outcome ("Fixed and verified" only for `resolved`).
    `action_taken` is None when nothing ran. `planted_in` names where host text tried to give
    Bleep orders (`graph/planted.py`), the places its fixed follow-up names; code sets it, never
    the model. `cause_en` is the cause in English when the report is written in another language
    (#374), the one memory keeps; None in English. `shown` holds the fields that are a fixed
    text of Bleep's as the templates they were made from, so the API and the notifier show them
    in the person's language (#427); the fields themselves stay English, as memory keeps them.
    """

    title: str
    summary: str
    cause: str
    cause_en: str | None = None
    action_taken: str | None = None
    outcome: str
    timeline: list[TimelineEntry] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    human_decision: str | None = None
    planted_in: list[PlantedPlace] = Field(default_factory=list)
    shown: dict[ReportText, Shown] = Field(default_factory=dict)
