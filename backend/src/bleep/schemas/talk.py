"""Talking to Bleep (#390): what the model reads from a message, what Bleep answers, and the
snapshot of Bleep's own state the answer comes from (docs/design/notify.md)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bleep.schemas.api import Health
from bleep.schemas.quiet import QuietMode, QuietScope

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
    """One exchange of a conversation: what the person said and what Bleep answered, and the
    incident it was about if any (#422), with `incident_at`, when the conversation took that
    incident up: a reply about it, or a look's press that opened it; a follow-up carries the
    time on. The model reads `said` and `answered` only."""

    said: str
    answered: str
    incident_id: str | None = None
    incident_at: datetime | None = None


# The actions a chat can offer to confirm: all of them, a look on request (#403) included.
OfferKind = ActionKind


class Offer(BaseModel):
    """An action checked against Bleep's state, ready to be confirmed with one button: every
    field comes from Bleep's state or from code, never from the model's words, except `text`,
    the person's own words for a plan (`instruct`). `subject` and `incident` are names as Bleep
    shows them; `until` is UTC. `plan_id` is the plan an instruction is for, bound by the
    surface that offers it (the plan a replied-to message showed, else the one that waits)."""

    kind: OfferKind
    scope: QuietScope | None = None
    target_id: str | None = None
    subject: str | None = None
    until: datetime | None = None
    incident_id: str | None = None
    incident: str | None = None
    plan_id: str | None = None
    text: str | None = None


class TalkLink(BaseModel):
    """A page an answer names: a service or an incident, found by code where `label` (its name
    in Bleep's state) stands in the answer. The dashboard builds the link from `kind` and `id`;
    Telegram shows none."""

    kind: Literal["service", "incident"]
    id: str
    label: str


class TalkReply(BaseModel):
    """What a surface shows for a message. `action` is what the model read the person to ask
    for; nothing has run. `offer` is that action once code checked it, with `answer` its confirm
    line: the surface offers it with a button, and only the press runs it. `incident_id` is the
    incident the message was about, if any."""

    kind: ReplyKind
    answer: str
    action: TalkAction | None = None
    incident_id: str | None = None
    offer: Offer | None = None
    links: list[TalkLink] = Field(default_factory=list)
    exchange_id: int | None = None  # its entry in the talk log (`talk.log.TalkLog`, #411)


TalkSurface = Literal["panel", "telegram"]


class TalkUsage(BaseModel):
    """What reading one message cost, summed over its model calls (a retry or the JSON fallback
    adds one); `usd` is None when no call's cost is known."""

    model: str
    calls: int
    tokens_in: int
    tokens_out: int
    usd: float | None = None


class TalkPress(BaseModel):
    """The press that took an offer and the line it was answered with; `line` stays empty
    while what it runs is still running, or if that failed. A look's press keeps the incident
    it opened or found open, and `ended`, the panel's summary once that incident ended (#458)."""

    at: datetime
    yes: bool
    line: str = ""
    incident_id: str | None = None
    ended: str = ""


class TalkExchange(BaseModel):
    """One message in a chat and what came of it, as the talk log keeps it (#411). `text` is the
    person's message as Bleep read it: control characters stripped, secrets redacted, capped.
    `reading` is what the model read, None when it could not read it (`error` names the error's
    type). `offer` is what code made of an action, or `refused` the fixed line a check answered
    with instead. `answer` and `links` are what the surface showed, `press` the first press of
    the offer's buttons, `usage` what the reading cost."""

    id: int
    at: datetime
    surface: TalkSurface
    text: str
    incident_id: str | None = None
    kind: ReplyKind
    reading: TalkReading | None = None
    error: str | None = None
    offer: Offer | None = None
    refused: str | None = None
    answer: str
    links: list[TalkLink] = Field(default_factory=list)
    confirm_id: str | None = Field(default=None, exclude=True)  # how a press finds its entry
    press: TalkPress | None = None
    usage: TalkUsage | None = None


MESSAGE_MAX_CHARS = 1000  # what a person may write in one message, on either surface
INCIDENT_ID_MAX_CHARS = 64


class ChatMessage(BaseModel):
    """A message written in the dashboard's panel; `incident_id` is the incident page it was
    written on, while its chip is kept."""

    text: str = Field(min_length=1, max_length=MESSAGE_MAX_CHARS)
    # An incident id is `inc_` and 18 letters and digits; anything far off is refused before it
    # is looked up or kept in the talk log.
    incident_id: str | None = Field(
        default=None, min_length=1, max_length=INCIDENT_ID_MAX_CHARS, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @field_validator("text", mode="before")
    @classmethod
    def _stripped(cls, text: object) -> object:
        """Spaces alone are no message: the length is checked on the words."""
        return text.strip() if isinstance(text, str) else text


class ChatConfirm(BaseModel):
    """The two buttons under an offer in the panel: pressing one posts `yes` to
    `/api/talk/confirms/{id}`. It expires at `expires_at`, or when `incident_id` ends."""

    id: str
    yes_label: str
    no_label: str
    expires_at: datetime
    incident_id: str | None = None


class ChatAnswer(BaseModel):
    """What the panel shows for a message: the answer, the pages it names, and the offer's
    buttons if it is one. Nothing has changed yet."""

    answer: str
    links: list[TalkLink] = Field(default_factory=list)
    confirm: ChatConfirm | None = None


class ConfirmPress(BaseModel):
    yes: bool


class ConfirmResult(BaseModel):
    """What a press did, in one line; the buttons turn into it. `incident_id` is the incident a
    look's press opened or found open, which the panel then follows (#458)."""

    line: str
    incident_id: str | None = None


# `running` until a plan waits for a yes (`waiting`) or the incident ends (`ended`); a waiting
# look goes on running once the plan is decided.
LookState = Literal["running", "waiting", "ended"]


class LookProgress(BaseModel):
    """How a look the panel follows goes (#458), all of it fixed words filled from Bleep's own
    state: `line` says what the run does now, or, while its plan waits and once it ended, sums
    it up with the report's (or the diagnosis') words as one redacted line. `steps` is what the
    run did so far, in order, repeats folded ("What I did"). `links` are the pages `line`
    names."""

    state: LookState
    line: str
    links: list[TalkLink] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


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


class CheckLine(BaseModel):
    """One thing the incident's run looked at (#422), built by code from its events: a tool it
    called, on what (`target`: a container, an app, a command, a query; `minutes`: how far back)
    and the first line of what came back, or one of Verify's checks after a fix (`verify_url`,
    `verify_container`, ...) and whether it passed. `result` is None while a call has no
    answer, or when its first line says nothing about what was found (a guard's note, a tool's
    fixed head). `target` and `minutes` are the arguments the model chose; `result` is host
    text."""

    tool: str
    target: str | None = None
    minutes: int | None = None
    result: str | None = None


class IncidentFacts(IncidentLine):
    """The incident a message is about, in more detail: what opened it, what its run checked,
    the plan that waits, what was said on its timeline and its report's summary."""

    signals: list[SignalLine] = Field(default_factory=list)
    checks: list[CheckLine] = Field(default_factory=list)
    plan: list[StepLine] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    summary: str | None = None
    # How many of its run's oldest checks were left out to keep the incident small (#422).
    left_out: dict[str, int] = Field(default_factory=dict)


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
