"""Settings the agent reads (which models to call, how much it may spend, when a signal is an
incident) and the overrides a person may set from the dashboard."""

import re
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

from bleep.hosts.config import MAX_EXITS, UrlCheckConfig, check_exits, check_url_syntax


def _env(name: str, field: str) -> AliasChoices:
    return AliasChoices(name, field)


MODEL_KEY_ENV = "OPENROUTER_API_KEY"  # `ModelSettings.openrouter_api_key`
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"  # `ModelSettings.anthropic_api_key`
ModelProvider = Literal["openrouter", "anthropic", "local"]
OPENROUTER_DEFAULT_MODEL = "openai/gpt-6-luna"
ANTHROPIC_DEFAULT_MODEL = "claude-haiku-5-5"
ANTHROPIC_DEFAULT_CHEAP_MODEL = "claude-haiku-5-5"
# Ids the settings page offers per provider as examples, so a person can pick one instead of
# looking it up; any id can still be typed. From OpenRouter's public model list and Anthropic's
# model ids, read 2026-11-12 (#517).
MODEL_EXAMPLES: dict[ModelProvider, tuple[str, ...]] = {
    "openrouter": ("openai/gpt-6-luna", "openai/gpt-6.1-terra", "openai/gpt-6.1-sol"),
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
    "local": ("openai/gpt-oss-20b",),
}


class ModelSettings(BaseSettings):
    """Model choice, read from the unprefixed env names in `.env.example` (`LLM_MODEL`, ...)."""

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", populate_by_name=True)

    provider: ModelProvider = Field("openrouter", validation_alias=_env("LLM_PROVIDER", "provider"))
    model: str = Field(OPENROUTER_DEFAULT_MODEL, validation_alias=_env("LLM_MODEL", "model"))
    cheap_model: str = Field(
        OPENROUTER_DEFAULT_MODEL, validation_alias=_env("LLM_CHEAP_MODEL", "cheap_model")
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
    anthropic_api_key: SecretStr | None = Field(
        None, validation_alias=_env("ANTHROPIC_API_KEY", "anthropic_api_key")
    )

    def resolved(self) -> ModelSettings:
        """With the Anthropic provider, an id that is still the OpenRouter default is Claude's:
        the default follows the provider. Any other id is the person's own and stays. The OpenRouter
        default is not an Anthropic id, so an id equal to it can only be one nobody chose for
        Anthropic: unset in `.env`, or left as it was when the provider was switched."""
        if self.provider != "anthropic":
            return self
        update = {
            name: claude
            for name, claude in (
                ("model", ANTHROPIC_DEFAULT_MODEL),
                ("cheap_model", ANTHROPIC_DEFAULT_CHEAP_MODEL),
            )
            if getattr(self, name) == OPENROUTER_DEFAULT_MODEL
        }
        return self.model_copy(update=update)


class Budgets(BaseModel):
    """Limits that keep an incident, and a day, from running away."""

    investigate_tool_calls: int = 8
    low_confidence: float = 0.6
    verify_loops: int = 2
    # Times one run may go back to Propose from Approval or Execute (an instruction, a target that
    # cannot be read, a plan gone stale); one more ends the run with the outcome `replan_limit`.
    replans: int = 3
    incident_tokens: int = 300_000
    daily_cost_usd: float = 1.0
    # Chat answers (`talk`) have a cap of their own and never count against the one above (#401).
    chat_daily_cost_usd: float = 0.2
    approval_reminder_min: int = 60  # a pending approval is re-sent this often, never approved
    model_timeout_s: int = 120  # one model call is cut off after this, then retried
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
    fold_window_h: int = 24  # a repeat within this many hours of the last joins its group; 0 off


# What a person may change from the dashboard (#36). Each field left out keeps the value below it:
# the hosts file, else the defaults above. Bounds keep a typo from switching a guard off.
ModelName = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[\w./:@-]+$")]
RuleText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_plain_line(name: str) -> str:
    if _CONTROL.search(name):
        raise PydanticCustomError(
            "one_plain_line", "Value error, must be one line without control characters"
        )
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
    """The model choice. The local model's base URL and every API key are kept in the secret
    file, not here (`store/secrets.py`, #372)."""

    provider: ModelProvider | None = None
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
    chat_daily_cost_usd: float | None = Field(None, ge=0, le=100)
    approval_reminder_min: int | None = Field(None, ge=5, le=1_440)
    model_timeout_s: int | None = Field(None, ge=10, le=600)
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
    fold_window_h: int | None = Field(None, ge=0, le=24 * 30)


class ServiceSettings(_Overrides):
    """One service's overrides. `urls`, `rules` and `exits` replace the hosts file's; protected
    containers are added to the hosts file's, which the dashboard can never unprotect."""

    urls: list[UrlCheckConfig] | None = Field(None, max_length=20)
    rules: list[RuleText] | None = Field(None, max_length=50)
    protected_containers: list[ContainerName] | None = Field(None, max_length=100)
    exits: list[str] | None = Field(None, max_length=MAX_EXITS)

    @field_validator("exits")
    @classmethod
    def _exits(cls, exits: list[str] | None) -> list[str] | None:
        return check_exits(exits) if exits is not None else None

    @field_validator("urls")
    @classmethod
    def _checkable(cls, urls: list[UrlCheckConfig] | None) -> list[UrlCheckConfig] | None:
        for cfg in urls or []:
            if check_url_syntax(cfg.url) is not None:
                raise PydanticCustomError(
                    "url_check_invalid",
                    "Value error, invalid url: must be http:// or https:// with a host and a "
                    "valid port",
                )
        return urls


class HostSettings(_Overrides):
    rules: list[RuleText] | None = Field(None, max_length=50)
    may_sleep: bool | None = None
    services: dict[ServiceKey, ServiceSettings] = Field(default_factory=dict, max_length=200)


def _known_zone(name: str) -> str:
    """An IANA time zone this machine knows; UTC needs no zone data."""
    if name != "UTC":
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError, OSError:
            raise PydanticCustomError(
                "time_zone", "Value error, must be an IANA time zone, e.g. Europe/Vienna"
            ) from None
    return name


TimeZoneName = Annotated[
    str, StringConstraints(min_length=1, max_length=64), AfterValidator(_known_zone)
]


# The language Bleep writes for the person in (#373); English when none is chosen.
Language = Literal["en", "de"]
DEFAULT_LANGUAGE: Language = "en"


class GeneralOverrides(_Overrides):
    # The zone every time is shown in, on Telegram and the dashboard (#400); stored times stay UTC.
    timezone: TimeZoneName | None = None
    language: Language | None = None


# `local` is the model baked into the image, `openai` an OpenAI-compatible endpoint that takes a
# key, `server` one on this machine or the network that takes none (LM Studio, Ollama; #442).
EmbeddingsProvider = Literal["local", "openai", "server"]
TracingKind = Literal["off", "langfuse", "langsmith"]


def has_userinfo(url: str) -> bool:
    """Whether `url` names a user or password before its host."""
    return "@" in url.split("://", 1)[-1].split("/", 1)[0]


def url_origin(url: str) -> str:
    """`scheme://host[:port]` of `url`, without what follows: a path or a query can hold a
    credential, so an address from the env is shown only this far."""
    authority = url.split("://", 1)[-1].split("/", 1)[0].rpartition("@")[2]
    scheme = url.split("://", 1)[0] if "://" in url else "https"
    return f"{scheme}://{authority}"


def _plain_url(url: str) -> str:
    """An http(s) URL with a host and no user or password; where it may point is the API's to
    check (`api/settings.py`)."""
    if check_url_syntax(url) is not None or has_userinfo(url):
        raise PydanticCustomError(
            "plain_url",
            "Value error, must be an http:// or https:// URL with a host and no password",
        )
    return url.rstrip("/")


# The prefixes of the tracers' keys, an OpenAI-style `sk-` key (20 or more characters after it,
# so `sk-team` is a name), and a long unbroken run of letters and digits mixed, as a token has,
# though not a UUID's 32 hex digits: a key pasted into a project field must not be stored (#372).
_KEY_PREFIXES = ("lsv2_", "pk-lf-", "sk-lf-")
_OPENAI_KEY = re.compile(r"sk-[\w-]{20,}", re.IGNORECASE)
_TOKEN_RUN = re.compile(r"[A-Za-z0-9]{32,}")
_UUID_HEX = re.compile(r"[0-9a-fA-F]{32}")


def _token(run: str) -> bool:
    mixed = any(c.isdigit() for c in run) and any(c.isalpha() for c in run)
    return mixed and not _UUID_HEX.fullmatch(run)


def _not_a_key(name: str) -> str:
    tokens = any(_token(run[0]) for run in _TOKEN_RUN.finditer(name))
    if name.lower().startswith(_KEY_PREFIXES) or _OPENAI_KEY.match(name) or tokens:
        raise PydanticCustomError(
            "looks_like_key", "Value error, looks like a key, not a project; keys go in the secrets"
        )
    return name


SettingsUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(_plain_url),
]
ProjectName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200, pattern=r"^[\w.:-]+$"),
    AfterValidator(_not_a_key),
]
# The tracing fields that are addresses; one from the env is shown as its origin only.
TRACING_URLS = ("langfuse_base_url", "langfuse_ui_url", "langsmith_endpoint")

# The env variable behind each field the env can decide; the env wins over the dashboard (#372).
ENV_NAMES: dict[str, dict[str, str]] = {
    "models": {
        "provider": "LLM_PROVIDER",
        "model": "LLM_MODEL",
        "cheap_model": "LLM_CHEAP_MODEL",
        "local_model": "LOCAL_MODEL",
    },
    "embeddings": {
        "provider": "EMBEDDINGS_PROVIDER",
        "model": "EMBEDDINGS_MODEL",
        "min_score": "EMBEDDINGS_MIN_SCORE",
    },
    "tracing": {
        "tracing": "TRACING",
        "langfuse_base_url": "LANGFUSE_BASE_URL",
        "langfuse_ui_url": "LANGFUSE_UI_URL",
        "langfuse_project_id": "LANGFUSE_PROJECT_ID",
        "langsmith_endpoint": "LANGSMITH_ENDPOINT",
        "langsmith_project": "LANGSMITH_PROJECT",
    },
}
# A tracer's address and the keys sent to it: the address changes only while they are unset.
TRACING_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "langfuse_base_url": ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
    "langsmith_endpoint": ("LANGSMITH_API_KEY",),
}
TRACER_OF_ENDPOINT = {"langfuse_base_url": "langfuse", "langsmith_endpoint": "langsmith"}
LOCAL_URL_ENV = "LOCAL_BASE_URL"
EMBEDDINGS_URL_ENV = "EMBEDDINGS_BASE_URL"


class EmbeddingsOverrides(_Overrides):
    """The embedder for memory and knowledge search (#372). Its endpoint's base URL and key are
    kept in the secret file. A change applies at the next start, which re-embeds the memory."""

    provider: EmbeddingsProvider | None = None
    model: ModelName | None = None  # the endpoint's model; the local one is baked into the image
    min_score: float | None = Field(None, ge=0, le=1)


class TracingOverrides(_Overrides):
    """Where incident runs and chat readings are traced (#372); the keys are in the secret file."""

    tracing: TracingKind | None = None
    langfuse_base_url: SettingsUrl | None = None
    langfuse_ui_url: SettingsUrl | None = None
    langfuse_project_id: ProjectName | None = None
    langsmith_endpoint: SettingsUrl | None = None
    langsmith_project: ProjectName | None = None


class SettingsUpdate(_Overrides):
    """`PUT /api/settings`. A section given replaces that section's overrides, and under `hosts`
    each host given replaces that host's; `{}` clears one. Sections left out stay as they are."""

    models: ModelOverrides | None = None
    budgets: BudgetOverrides | None = None
    thresholds: ThresholdOverrides | None = None
    general: GeneralOverrides | None = None
    embeddings: EmbeddingsOverrides | None = None
    tracing: TracingOverrides | None = None
    hosts: dict[str, HostSettings] | None = None


class ModelChoice(BaseModel):
    provider: ModelProvider
    model: str
    cheap_model: str
    local_model: str


class ProviderModels(BaseModel):
    """The ids that would run with one provider (`model` and `cheap_model` for a hosted one,
    `local_model` for a local server), and example ids it takes."""

    model: str
    cheap_model: str
    local_model: str
    examples: list[str]


class EmbeddingsChoice(BaseModel):
    """The embedder in force. `model` is the endpoint's (None: the local model); `min_score` None
    is the model's measured cut-off (`memory.embeddings.min_score_for`)."""

    provider: EmbeddingsProvider = "local"
    model: str | None = None
    min_score: float | None = None


class TracingChoice(BaseModel):
    """The tracer in force, without its keys; the defaults are `llm.tracing.TracingSettings`'s."""

    tracing: TracingKind = "off"
    langfuse_base_url: str = "http://langfuse-web:3000"
    langfuse_ui_url: str = "http://localhost:3001"
    langfuse_project_id: str | None = None
    langsmith_endpoint: str | None = None
    langsmith_project: str = "bleep"


class SecretStatus(BaseModel):
    """A secret Bleep reads: the environment variable or the file it comes from, and whether it
    is there. Never its value."""

    env: str | None = None
    file: str | None = None
    set: bool
    # Where a value that can be set from the dashboard comes from (`GET /api/secrets` has more).
    source: Literal["env", "dashboard"] | None = None


class HostConnectionView(BaseModel):
    """How Bleep reaches a host. `source` says who decides it: the hosts file (read-only on the
    dashboard) or the dashboard, whose hosts carry their key and confirmed host key as set or
    not (never their content) and the `version` a change sends in `If-Match`."""

    source: Literal["file", "dashboard"] = "file"
    kind: Literal["ssh", "socket"]
    address: str | None = None
    user: str | None = None
    port: int | None = None
    docker_socket: str | None = None  # the socket on an ssh host
    endpoint: str | None = None  # a socket host's unix:// or tcp:// endpoint
    key_file: SecretStatus | None = None
    known_hosts_file: str | None = None
    ssh_key: SecretStatus | None = None  # a dashboard host's private key
    host_key: SecretStatus | None = None  # a dashboard host's confirmed host key
    version: str | None = None


class ServiceSettingsView(BaseModel):
    urls: list[UrlCheckConfig]
    rules: list[str]
    protected_containers: list[str]  # the hosts file's and the dashboard's
    hosts_file_protected_containers: list[str]  # those the dashboard cannot unprotect
    exits: list[str] = []  # jobs meant to finish: the dashboard's list, else the hosts file's


class HostSettingsView(BaseModel):
    id: str
    name: str
    connection: HostConnectionView
    rules: list[str]
    may_sleep: bool  # in force: the dashboard's choice, else the hosts file's
    services: dict[str, ServiceSettingsView]


class SettingsView(BaseModel):
    """`GET /api/settings`: the values in force, and the database's overrides that changed them."""

    models: ModelChoice
    # Per provider, the ids that would run if it were chosen with what is stored now, so the page
    # shows what runs for the provider in its select before a save (#517).
    models_by_provider: dict[ModelProvider, ProviderModels]
    model_key: SecretStatus  # the key's variable of the provider in force; a local model needs none
    budgets: Budgets
    thresholds: Thresholds
    hosts: list[HostSettingsView]
    plugins: dict[str, bool]
    # The zone times are shown in: the dashboard's choice, else BLEEP_TIMEZONE; None shows the
    # dashboard in the browser's zone and Telegram in UTC.
    timezone: str | None = None
    language: Language = DEFAULT_LANGUAGE  # the dashboard's choice, else English
    embeddings: EmbeddingsChoice = EmbeddingsChoice()
    tracing: TracingChoice = TracingChoice()
    # Every field the env decides, as `section.field` ("models.provider"): it wins over the
    # dashboard, which shows it as set in .env and cannot change it (#372).
    from_env: list[str] = []
    # Sections saved here that are in force only after a restart: `embeddings` (#372).
    restart_needed: list[str] = []
    overrides: dict[str, Any]  # a `SettingsUpdate` without the fields left at the layers below
    version: str  # of `overrides`; a `PUT /api/settings` sends it back in `If-Match`
