from datetime import UTC, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator
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
    # Ticks after Bleep's own machine wakes in which a host not answering is not counted.
    wake_grace_ticks: int = Field(default=4, ge=0, le=40)
    cookie_secure: bool = False  # true once the dashboard is served over https
    dev_panel: bool = False  # the dev panel's routes and nav entry (docs/design/dev-panel.md)
    # Where a person's browser reaches the dashboard; the end of an incident on Telegram links to
    # its page there. Unset, the message has no link (docs/design/notify.md, #380).
    dashboard_url: AnyHttpUrl | None = None
    # The IANA time zone times are shown in until one is chosen on the settings page, which wins
    # (#400): Telegram's "until tomorrow 08:00" and "Quiet until 14:30" (#389) and the dashboard.
    # Unset everywhere, Telegram's times are UTC and say so and the dashboard uses the browser's.
    timezone: str | None = None
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
    # Where memory recall cuts off; unset, the model's measured one (memory.embeddings.MIN_SCORES).
    embeddings_min_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
        validation_alias=AliasChoices("EMBEDDINGS_MIN_SCORE", "embeddings_min_score"),
    )

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, name: str | None) -> str | None:
        if name is None or name == "UTC":
            return name
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                "BLEEP_TIMEZONE must be an IANA time zone, e.g. Europe/Vienna"
            ) from None
        return name

    @property
    def zone(self) -> tzinfo | None:
        """The configured zone; None when unset (times are UTC then, and say so). UTC needs no
        zone data, so an image without it still starts."""
        if self.timezone is None:
            return None
        return UTC if self.timezone == "UTC" else ZoneInfo(self.timezone)


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
