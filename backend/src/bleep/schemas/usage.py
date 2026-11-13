"""The Usage page's numbers, as `GET /api/usage` answers (docs/design/app.md, "Usage page")."""

from typing import Literal

from pydantic import BaseModel, Field

from bleep.schemas.graph import Outcome

# `unknown` for a row written before the ledger recorded the provider.
UsageProvider = Literal["anthropic", "openrouter", "local", "unknown"]
UsageRange = Literal[1, 7, 30, 90]


class UsageTotals(BaseModel):
    """`cost_usd` sums the calls whose cost is known; `unpriced_calls` counts the others.
    `local_calls` counts the calls to a local model, which are free: one of a model Bleep has no
    price for is unpriced too. `chat_calls` are the answers in `talk`; the other calls
    (`incident_calls`) belong to an incident or to a recovery line."""

    cost_usd: float
    calls: int
    incident_calls: int
    chat_calls: int
    unpriced_calls: int
    local_calls: int
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    cache_write_tokens: int


class ModelUsage(BaseModel):
    """One model through one provider, over the range."""

    model: str
    provider: UsageProvider
    calls: int
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    unpriced_calls: int


class DayUsage(BaseModel):
    """One local day's calls of one model; a day or model without a call has no row."""

    day: str  # YYYY-MM-DD
    model: str
    cost_usd: float
    incident_calls: int
    chat_calls: int


class CostliestIncident(BaseModel):
    incident_id: str
    name: str  # its short name, or its title for an incident opened before there were any
    cost_usd: float


class IncidentUsage(BaseModel):
    """The incidents opened in the range. `average_cost_usd` is over those with a priced call and
    null when none has one."""

    count: int
    average_cost_usd: float | None
    most_expensive: CostliestIncident | None


class IncidentEndings(BaseModel):
    """Every incident opened in the range is in exactly one count: by the outcome its report
    states, else `failed` (the run failed), `open` (still running or waiting) or `other` (finished
    with no report, or a report whose outcome is none of the fixed ones)."""

    outcomes: dict[Outcome, int]
    failed: int
    open: int
    other: int


class ActionCount(BaseModel):
    """Steps in the action log by action and status (`started`, `done`, `failed`, `skipped`)."""

    action: str
    status: str
    count: int


class GuardCount(BaseModel):
    """What the guard log holds by kind; a kind with no entry has no row."""

    kind: str
    count: int


class TodayCaps(BaseModel):
    """Today's spend against the daily caps, whatever the range: `spent_usd` is the incidents' and
    recovery lines' (the cap that stops an investigation), `chat_spent_usd` the chat answers'."""

    spent_usd: float
    daily_cap_usd: float
    chat_spent_usd: float
    chat_daily_cap_usd: float


class UsageView(BaseModel):
    days: UsageRange
    totals: UsageTotals
    models: list[ModelUsage]
    per_day: list[DayUsage]
    incidents: IncidentUsage
    endings: IncidentEndings
    actions: list[ActionCount] = Field(default_factory=list)
    guard: list[GuardCount] = Field(default_factory=list)
    today: TodayCaps
