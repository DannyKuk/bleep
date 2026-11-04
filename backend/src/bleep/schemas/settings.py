"""Settings the agent reads (which models to call, how much it may spend, when a signal is an
incident) and the overrides a person may set from the dashboard."""

import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from bleep.hosts.config import UrlCheckConfig, check_url_syntax


def _env(name: str, field: str) -> AliasChoices:
    return AliasChoices(name, field)


class ModelSettings(BaseSettings):
    """Model choice, read from the unprefixed env names in `.env.example` (`LLM_MODEL`, ...)."""

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", populate_by_name=True)

    provider: Literal["openrouter", "local"] = Field(
        "openrouter", validation_alias=_env("LLM_PROVIDER", "provider")
    )
    model: str = Field("openai/gpt-6-luna", validation_alias=_env("LLM_MODEL", "model"))
    cheap_model: str = Field(
        "openai/gpt-6-luna", validation_alias=_env("LLM_CHEAP_MODEL", "cheap_model")
    )
    local_base_url: str = Field(
        "http://host.docker.internal:1234/v1",
        validation_alias=_env("LOCAL_BASE_URL", "local_base_url"),
    )
    local_model: str = Field(
        "openai/gpt-oss-20b", validation_alias=_env("LOCAL_MODEL", "local_model")
    )
    temperature: float = 0
    openrouter_api_key: SecretStr | None = Field(
        None, validation_alias=_env("OPENROUTER_API_KEY", "openrouter_api_key")
    )


class Budgets(BaseModel):
    """Limits that keep an incident, and a day, from running away."""

    investigate_tool_calls: int = 8
    low_confidence: float = 0.6
    verify_loops: int = 2
    # Times one run may go back to Propose from Approval or Execute (an instruction, a target that
    # cannot be read, a plan gone stale); one more ends the run with the outcome `replan_limit`.
    replans: int = 3
    incident_tokens: int = 150_000
    daily_cost_usd: float = 1.0
    approval_reminder_min: int = 60  # a pending approval is re-sent this often, never approved
    model_timeout_s: float = 120  # one model call is cut off after this, then retried
    paused_retry_min: int = 5  # a run paused on an unavailable model is tried again this often
    stuck_after_min: int = 30  # an incident that has not moved this long gets one notice


class Thresholds(BaseModel):
    """When a signal becomes an incident. Seconds unless the name says otherwise."""

    container_grace_s: int = 60
    repeated_deaths: int = 3
    repeated_window_s: int = 600
    url_failures_to_open: int = 2
    url_slow_ms: int = 5000
    disk_percent: float = 90
    memory_available_percent_min: float = 5
    memory_polls: int = 3
    load_per_cpu: float = 2.0
    load_polls: int = 3
    host_unreachable_s: int = 300
    debounce_s: int = 30


# What a person may change from the dashboard (#36). Each field left out keeps the value below it:
# the hosts file, else the defaults above. Bounds keep a typo from switching a guard off.
ModelName = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[\w./:@-]+$")]
RuleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_plain_line(name: str) -> str:
    if _CONTROL.search(name):
        raise ValueError("must be one line without control characters")
    return name


# A container's name, or a Coolify application's as written in Coolify (`Shop API`, `static:main`),
# which the plan check slugs.
ContainerName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    AfterValidator(_one_plain_line),
]
ServiceKey = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class _Overrides(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelOverrides(_Overrides):
    """The model choice; the base URL of a local model and every API key stay in the env."""

    provider: Literal["openrouter", "local"] | None = None
    model: ModelName | None = None
    cheap_model: ModelName | None = None
    local_model: ModelName | None = None


class BudgetOverrides(_Overrides):
    investigate_tool_calls: int | None = Field(None, ge=1, le=50)
    low_confidence: float | None = Field(None, ge=0.1, le=0.95)
    verify_loops: int | None = Field(None, ge=0, le=5)
    replans: int | None = Field(None, ge=0, le=10)
    incident_tokens: int | None = Field(None, ge=1_000, le=2_000_000)
    daily_cost_usd: float | None = Field(None, ge=0, le=100)
    approval_reminder_min: int | None = Field(None, ge=5, le=1_440)
    model_timeout_s: float | None = Field(None, ge=10, le=600)
    paused_retry_min: int | None = Field(None, ge=1, le=240)
    stuck_after_min: int | None = Field(None, ge=5, le=1_440)


class ThresholdOverrides(_Overrides):
    container_grace_s: int | None = Field(None, ge=0, le=3_600)
    repeated_deaths: int | None = Field(None, ge=2, le=100)
    repeated_window_s: int | None = Field(None, ge=60, le=86_400)
    url_failures_to_open: int | None = Field(None, ge=1, le=20)
    url_slow_ms: int | None = Field(None, ge=100, le=60_000)
    disk_percent: float | None = Field(None, ge=50, le=99)
    memory_available_percent_min: float | None = Field(None, ge=1, le=50)
    memory_polls: int | None = Field(None, ge=1, le=60)
    load_per_cpu: float | None = Field(None, ge=0.5, le=20)
    load_polls: int | None = Field(None, ge=1, le=60)
    host_unreachable_s: int | None = Field(None, ge=30, le=86_400)
    debounce_s: int | None = Field(None, ge=0, le=3_600)


class ServiceSettings(_Overrides):
    """One service's overrides. `urls` and `rules` replace the hosts file's; protected
    containers are added to the hosts file's, which the dashboard can never unprotect."""

    urls: list[UrlCheckConfig] | None = Field(None, max_length=20)
    rules: list[RuleText] | None = Field(None, max_length=50)
    protected_containers: list[ContainerName] | None = Field(None, max_length=100)

    @field_validator("urls")
    @classmethod
    def _checkable(cls, urls: list[UrlCheckConfig] | None) -> list[UrlCheckConfig] | None:
        for cfg in urls or []:
            if check_url_syntax(cfg.url) is not None:
                raise ValueError(
                    "invalid url: must be http:// or https:// with a host and a valid port"
                )
        return urls


class HostSettings(_Overrides):
    rules: list[RuleText] | None = Field(None, max_length=50)
    services: dict[ServiceKey, ServiceSettings] = Field(default_factory=dict, max_length=200)


class SettingsUpdate(_Overrides):
    """`PUT /api/settings`. A section given replaces that section's overrides, and under `hosts`
    each host given replaces that host's; `{}` clears one. Sections left out stay as they are."""

    models: ModelOverrides | None = None
    budgets: BudgetOverrides | None = None
    thresholds: ThresholdOverrides | None = None
    hosts: dict[str, HostSettings] | None = None


class ModelChoice(BaseModel):
    provider: Literal["openrouter", "local"]
    model: str
    cheap_model: str
    local_model: str


class SecretStatus(BaseModel):
    """A secret Bleep reads: the environment variable or the file it comes from, and whether it
    is there. Never its value."""

    env: str | None = None
    file: str | None = None
    set: bool


class HostConnectionView(BaseModel):
    """How Bleep reaches a host, as the hosts file says; the dashboard never changes it."""

    kind: Literal["ssh", "socket"]
    address: str | None = None
    user: str | None = None
    port: int | None = None
    docker_socket: str | None = None  # the socket on an ssh host
    endpoint: str | None = None  # a socket host's unix:// or tcp:// endpoint
    key_file: SecretStatus | None = None
    known_hosts_file: str | None = None


class ServiceSettingsView(BaseModel):
    urls: list[UrlCheckConfig]
    rules: list[str]
    protected_containers: list[str]  # the hosts file's and the dashboard's
    hosts_file_protected_containers: list[str]  # those the dashboard cannot unprotect


class HostSettingsView(BaseModel):
    id: str
    name: str
    connection: HostConnectionView
    rules: list[str]
    services: dict[str, ServiceSettingsView]


class SettingsView(BaseModel):
    """`GET /api/settings`: the values in force, and the database's overrides that changed them."""

    models: ModelChoice
    model_key: SecretStatus  # the OpenRouter key's variable; a local model needs none
    budgets: Budgets
    thresholds: Thresholds
    hosts: list[HostSettingsView]
    plugins: dict[str, bool]
    overrides: dict[str, Any]  # a `SettingsUpdate` without the fields left at the layers below
    version: str  # of `overrides`; a `PUT /api/settings` sends it back in `If-Match`
