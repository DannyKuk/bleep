"""The HTTP shapes of the API. `/openapi.json` carries them to the dashboard."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

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
from bleep.schemas.quiet import QuietMode, QuietScope, QuietState

# `asleep`: a host that may sleep (a laptop) and does not answer, and every service on it.
# `done`: a job that ran to its end (exit 0, a one-shot or named in its service's `exits`).
Health = Literal["ok", "degraded", "down", "stopped", "done", "unknown", "asleep"]


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

    @classmethod
    def of(cls, state: QuietState) -> Self:
        return cls(scope=state.scope, mode=state.mode, until=state.until, since=state.since)


class WatchView(BaseModel):
    """A failure the watcher has seen and waits on before it opens an incident (#358), shown
    only while an incident would open once the wait is over (not quiet, asleep, already open or
    held as a repeat). `url`: `failures` checks in a row failed of the `failures_to_open` it
    needs. `container`: a death inside its grace window, which ends at `opens_after`.
    `unreachable`: the host has not answered since `since`, and its window ends at
    `opens_after`."""

    kind: Literal["url", "container", "unreachable"]
    since: datetime
    opens_after: datetime | None = None
    url: str | None = None
    container: str | None = None
    failures: int | None = None
    failures_to_open: int | None = None


UnwatchedReason = Literal["paused", "created", "before_start", "after_incident", "unknown"]


class UnwatchedView(BaseModel):
    """Why a service that shows down opens no incident (#478): it is down, the watcher waits on
    nothing for it, it has no incident open, it is not quiet and its host answers. `paused` and
    `created`: a container in that state (no death event comes). `before_start`: a container
    was already down in the watcher's first listing. `after_incident`: it has been down since
    before its latest incident ended (`incident_id`, `incident_short_name`). `unknown`: none of
    these. `container` is the container the reason is about, or the first one down."""

    reason: UnwatchedReason
    container: str | None = None
    incident_id: str | None = None
    incident_short_name: str | None = None


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
    may_sleep: bool = False
    quiet: QuietView | None = None
    quiet_service_ids: list[str] = []
    watching: WatchView | None = None


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


class UrlCheckPoint(BaseModel):
    """One past check of a URL, for the service page's strip: no error text, no body."""

    ok: bool
    status_code: int | None
    latency_ms: int
    checked_at: datetime


class UrlHistory(BaseModel):
    """A configured URL's recent checks, oldest first (the last 30 at most)."""

    url: str
    checks: list[UrlCheckPoint]


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
    watching: list[WatchView] = []
    unwatched: UnwatchedView | None = None


ApprovalStatus = Literal["pending", "approved", "rejected", "instructed", "closed"]


class ApprovalView(BaseModel):
    """An incident's latest approval: the plan put to the human and, once decided, the answer.

    `status` is `pending` until a channel decides it, or `closed` when the incident finished
    before anyone answered (#277); the snapshots stay on the server.
    """

    id: str
    status: ApprovalStatus
    plan_hash: str
    proposal: Proposal
    requested_at: datetime
    reminders: int
    decision: Decision | None


class IncidentVotes(BaseModel):
    """The human's vote on an incident's diagnosis and plan; null where there is none."""

    diagnosis: Vote | None = None
    proposal: Vote | None = None


class IncidentListItem(Incident):
    """An incident as the lists show it, with the human's votes (not their notes)."""

    votes: IncidentVotes = Field(default_factory=IncidentVotes)
    repeats: int = 1  # how many incidents its group of repeats holds (`fold_of`), itself included
    # Its kind as its fold key holds it (`host_unreachable`, `disk`, `death` for any death, ...);
    # null for an incident from before fold keys.
    kind: str | None = None


class IncidentDetail(BaseModel):
    """One incident with what its run has found so far; each part is null until it exists."""

    incident: Incident
    diagnosis: Diagnosis | None
    proposal: Proposal | None
    approval: ApprovalView | None
    outcome: Outcome | None
    report: Report | None
    feedback: list[Feedback] = Field(default_factory=list)  # the human's votes, by target
    # What "This is expected" would record, in one sentence; null when it is not offered.
    expectation: str | None = None
    repeats: int = 1  # how many incidents its group of repeats holds, itself included


class DecisionBody(BaseModel):
    """The human's answer from the dashboard to the approval with `approval_id`."""

    approval_id: str
    kind: Literal["approve", "reject", "instruct"]
    text: str | None = Field(default=None, max_length=2000)  # the instruction, for `instruct`

    @model_validator(mode="after")
    def _instruction_has_text(self) -> Self:
        text = (self.text or "").strip()
        if self.kind == "instruct" and not text:
            raise PydanticCustomError(
                "instruction_needs_text", "Value error, an instruction needs text"
            )
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
            raise PydanticCustomError("fact_needs_text", "Value error, a fact needs text")
        return self


class ServiceDetail(BaseModel):
    """One service for its page: the overview's view of it, the rules Bleep follows for it (its
    own and its host's), since when it has had its current health (null when that is not known)
    and each configured URL's recent checks."""

    service: ServiceView
    rules: list[str]
    host_rules: list[str]
    health_since: datetime | None = None
    recent_checks: list[UrlHistory] = []
