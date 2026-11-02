"""Settings the agent reads: which models to call, and how much it may spend."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
