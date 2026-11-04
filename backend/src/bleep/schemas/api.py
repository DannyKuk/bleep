"""The HTTP shapes of the API. `/openapi.json` carries them to the dashboard."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from bleep.schemas.graph import (
    FACT_CHARS,
    NOTE_CHARS,
    Decision,
    Diagnosis,
    Feedback,
    FeedbackTarget,
    Outcome,
    Proposal,
    Report,
    Vote,
)
from bleep.schemas.incidents import Incident
from bleep.schemas.quiet import QuietMode, QuietScope

Health = Literal["ok", "degraded", "down", "stopped", "unknown"]


class Me(BaseModel):
    authenticated: bool


class DiskView(BaseModel):
    mount: str
    used_percent: float


class BasicsView(BaseModel):
    os: str
    # None when the host could not tell (a socket host has no load; a parse can fail)
    cpus: int | None
    mem_total: int | None
    mem_available: int | None
    load1: float | None
    load15: float | None
    disks: list[DiskView]


class QuietView(BaseModel):
    """A snooze or a leave-alone; on a service, scope `host` means quiet through its host."""

    scope: QuietScope
    mode: QuietMode
    until: datetime | None
    since: datetime


class HostView(BaseModel):
    id: str
    name: str
    kind: Literal["socket", "ssh"]
    reachable: bool
    last_seen: datetime | None
    error: str | None
    basics: BasicsView | None
    service_count: int
    health: Health
    quiet: QuietView | None = None
    quiet_service_ids: list[str] = []


class ContainerView(BaseModel):
    id: str
    name: str
    image: str
    state: str
    health: Literal["healthy", "unhealthy", "starting", "none"]
    restart_count: int
    exit_code: int | None
    protected: bool


class UrlStatus(BaseModel):
    url: str
    status: Literal["unknown", "ok", "failing"]
    last_checked: datetime | None
    status_code: int | None
    latency_ms: int | None


class ServiceView(BaseModel):
    id: str
    host_id: str
    name: str
    source: Literal["coolify", "compose", "single"]
    health: Health
    containers: list[ContainerView]
    urls: list[UrlStatus]
    open_incident_id: str | None
    quiet: QuietView | None = None


class ApprovalView(BaseModel):
    """An incident's latest approval: the plan put to the human and, once decided, the answer.

    `status` is `pending` until a channel decides it; the snapshots stay on the server.
    """

    id: str
    status: Literal["pending", "approved", "rejected", "instructed"]
    plan_hash: str
    proposal: Proposal
    requested_at: datetime
    reminders: int
    decision: Decision | None


class IncidentDetail(BaseModel):
    """One incident with what its run has found so far; each part is null until it exists."""

    incident: Incident
    diagnosis: Diagnosis | None
    proposal: Proposal | None
    approval: ApprovalView | None
    outcome: Outcome | None
    report: Report | None
    feedback: list[Feedback] = Field(default_factory=list)  # the human's votes, by target


class DecisionBody(BaseModel):
    """The human's answer from the dashboard to the approval with `approval_id`."""

    approval_id: str
    kind: Literal["approve", "reject", "instruct"]
    text: str | None = Field(default=None, max_length=2000)  # the instruction, for `instruct`

    @model_validator(mode="after")
    def _instruction_has_text(self) -> Self:
        text = (self.text or "").strip()
        if self.kind == "instruct" and not text:
            raise ValueError("an instruction needs text")
        self.text = text if self.kind == "instruct" else None
        return self


class DecisionConflict(BaseModel):
    """Why a decision was refused; `decided_via` names the channel that answered first."""

    detail: str
    decided_via: Literal["telegram", "dashboard"] | None = None


class FeedbackBody(BaseModel):
    """A thumbs up or down on the incident's diagnosis or plan; the note is trimmed and an empty
    one dropped."""

    target: FeedbackTarget
    vote: Vote
    note: str | None = Field(default=None, max_length=NOTE_CHARS)

    @model_validator(mode="after")
    def _trim(self) -> Self:
        self.note = (self.note or "").strip() or None
        return self


class FactBody(BaseModel):
    """A fact a person adds to a service; trimmed, and empty is refused."""

    text: str = Field(max_length=FACT_CHARS)

    @model_validator(mode="after")
    def _has_text(self) -> Self:
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("a fact needs text")
        return self


class ServiceDetail(BaseModel):
    """One service for its page: the overview's view of it plus the rules Bleep follows for it
    (its own and its host's)."""

    service: ServiceView
    rules: list[str]
    host_rules: list[str]
