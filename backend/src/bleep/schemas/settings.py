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
    incident_tokens: int = 150_000
    daily_cost_usd: float = 1.0
