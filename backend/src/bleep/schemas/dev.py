"""What the dev panel reads and writes: prompt overrides, cost per incident and per day, and the
guard log (#42). The model choice and the budgets go through `PUT /api/settings`."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from bleep.schemas.incidents import IncidentStatus

PROMPT_CHARS = 20_000  # the longest prompt file is under 4,000


class DevStatus(BaseModel):
    """`GET /api/dev`: answers only when the panel is on, so the dashboard shows its nav entry."""

    enabled: bool = True


class PromptView(BaseModel):
    name: str
    placeholders: list[str]  # the file's, which an override must use exactly
    file_text: str
    override: str | None = None
    overridden_at: datetime | None = None
    version: str  # of the override; a save or reset sends it back in `If-Match`


class PromptOverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=PROMPT_CHARS)


class Usage(BaseModel):
    """Sums of the usage ledger's rows. `usd` covers the calls with a known cost and is None when
    none has one; `unpriced` counts the calls without."""

    calls: int
    tokens_in: int
    tokens_out: int
    usd: float | None
    unpriced: int


class IncidentCost(Usage):
    incident_id: str
    # From the incident, when the ledger's id names one.
    title: str | None = None
    status: IncidentStatus | None = None
    opened_at: datetime | None = None
    trace_url: str | None = None


class DayCost(Usage):
    day: date  # the machine's local day, as the daily cap counts it


class CostView(BaseModel):
    incidents: list[IncidentCost]
    days: list[DayCost]
    daily_cap_usd: float
    spent_today_usd: float


class GuardEntryView(BaseModel):
    id: int
    incident_id: str
    kind: str
    detail: str
    at: datetime
