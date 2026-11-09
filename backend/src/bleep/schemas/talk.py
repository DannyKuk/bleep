"""Talking to Bleep (#390): what the model reads from a message, what Bleep answers, and the
snapshot of Bleep's own state the answer comes from (docs/design/notify.md)."""

from typing import Literal

from pydantic import BaseModel, Field

from bleep.schemas.api import Health
from bleep.schemas.quiet import QuietMode

TalkKind = Literal["question", "action", "refusal", "unclear"]
# `failed`: the message could not be read (the model failed or the day's cost cap is spent).
ReplyKind = Literal["question", "action", "refusal", "unclear", "failed"]
# The actions Bleep already has, as a person may ask for them in a chat. None of them runs a
# command or touches a container: every change on a host still goes through an approved plan.
ActionKind = Literal[
    "snooze",
    "leave_alone",
    "watch_again",
    "expected",
    "instruct",
    "show_plan",
    "look_again",
]
# What a refusal is about: a change Bleep makes only through a plan (a command, a container, a
# setting), or a secret or env value it never shows.
RefusalKind = Literal["change", "secret"]


class TalkAction(BaseModel):
    """One action the person asked for. `subject` is a service or host as the snapshot names it,
    `incident` an incident's short name; `minutes` is a duration ("for 2 hours"), `until` a local
    time `YYYY-MM-DD HH:MM` ("until 16:00")."""

    kind: ActionKind
    subject: str | None = None
    minutes: int | None = None
    until: str | None = None
    incident: str | None = None


class TalkReading(BaseModel):
    """The model's reading of one message: its kind, the answer to a question, the action asked
    for, or what a refusal is about."""

    kind: TalkKind
    answer: str = ""
    action: TalkAction | None = None
    refusal: RefusalKind | None = None


class Turn(BaseModel):
    """One exchange of a conversation: what the person said and what Bleep answered."""

    said: str
    answered: str


class TalkReply(BaseModel):
    """What a surface shows for a message. `action` is what Bleep understood the person to ask
    for; nothing has run. `incident_id` is the incident the message was about, if any."""

    kind: ReplyKind
    answer: str
    action: TalkAction | None = None
    incident_id: str | None = None


class QuietLine(BaseModel):
    mode: QuietMode
    until: str | None = None  # None: left alone until a person says otherwise
    since: str
    through_host: bool = False


class HostLine(BaseModel):
    id: str
    name: str
    health: Health
    reachable: bool
    last_seen: str | None = None
    quiet: QuietLine | None = None


class ContainerLine(BaseModel):
    name: str
    state: str
    health: str
    exit_code: int | None = None
    restarts: int = 0


class ServiceLine(BaseModel):
    id: str
    name: str
    host: str
    health: Health
    containers: list[ContainerLine] = Field(default_factory=list)
    quiet: QuietLine | None = None
    open_incident: str | None = None


class IncidentLine(BaseModel):
    """An incident as the snapshot lists it; `outcome` and `cause` once it ended."""

    id: str
    name: str | None = None
    title: str
    subject: str
    status: str
    opened: str
    ended: str | None = None
    plan_waiting: bool = False
    outcome: str | None = None
    cause: str | None = None


class SignalLine(BaseModel):
    kind: str
    container: str | None = None
    at: str


class StepLine(BaseModel):
    action: str
    target: str
    host: str


class IncidentFacts(IncidentLine):
    """The incident a message is about, in more detail: what opened it, the plan that waits,
    what was said on its timeline and its report's summary."""

    signals: list[SignalLine] = Field(default_factory=list)
    plan: list[StepLine] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    summary: str | None = None


class Snapshot(BaseModel):
    """Bleep's own state, built by code: times are local (`zone`) as `YYYY-MM-DD HH:MM`."""

    now: str
    zone: str
    hosts: list[HostLine] = Field(default_factory=list)
    services: list[ServiceLine] = Field(default_factory=list)
    open_incidents: list[IncidentLine] = Field(default_factory=list)
    ended_last_24h: list[IncidentLine] = Field(default_factory=list)
    # How many of each list were left out to keep the snapshot small; only parts that lost some.
    left_out: dict[str, int] = Field(default_factory=dict)
