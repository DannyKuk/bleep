from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_ADMIN_PASSWORD = 12


class SettingsError(ValueError):
    """The settings are not safe to start with."""


class Settings(BaseSettings):
    """Bleep's own settings, read from `BLEEP_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="BLEEP_", env_ignore_empty=True, extra="ignore")

    data_dir: Path = Path("/data")
    hosts_file: Path = Path("/config/hosts.yaml")
    admin_password: SecretStr
    secret: SecretStr
    watch_interval_s: float = 15  # seconds between watcher ticks per host
    cookie_secure: bool = False  # true once the dashboard is served over https
    dev_panel: bool = False  # the dev panel's routes and nav entry (docs/design/dev-panel.md)
    # Telegram is optional and has its own names in .env (no BLEEP_ prefix). Only this chat is
    # answered. The token is a secret: it stays out of the database, the bus and the logs.
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")
    # Embeddings for long-term memory, also without the prefix: the local model by default, or
    # any OpenAI-compatible `/embeddings` endpoint. A change re-embeds the memory on the next boot.
    embeddings_provider: Literal["local", "openai"] = Field(
        default="local", validation_alias=AliasChoices("EMBEDDINGS_PROVIDER", "embeddings_provider")
    )
    embeddings_model: str | None = Field(
        default=None, validation_alias=AliasChoices("EMBEDDINGS_MODEL", "embeddings_model")
    )
    embeddings_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("EMBEDDINGS_BASE_URL", "embeddings_base_url")
    )
    embeddings_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("EMBEDDINGS_API_KEY", "embeddings_api_key")
    )


def check_settings(settings: Settings) -> None:
    """Refuse to start with a login password that is quick to guess, or with an embeddings
    endpoint that is only half configured."""
    if len(settings.admin_password.get_secret_value()) < MIN_ADMIN_PASSWORD:
        raise SettingsError(
            f"BLEEP_ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD} characters; "
            "set a longer one in .env"
        )
    if settings.embeddings_provider == "openai" and not (
        settings.embeddings_base_url and settings.embeddings_model
    ):
        raise SettingsError(
            "EMBEDDINGS_PROVIDER=openai needs EMBEDDINGS_BASE_URL and EMBEDDINGS_MODEL in .env"
        )
