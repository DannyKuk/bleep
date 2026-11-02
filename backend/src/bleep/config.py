from pathlib import Path

from pydantic import Field, SecretStr
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
    # Telegram is optional and has its own names in .env (no BLEEP_ prefix). Only this chat is
    # answered. The token is a secret: it stays out of the database, the bus and the logs.
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")


def check_settings(settings: Settings) -> None:
    """Refuse to start with a login password that is quick to guess."""
    if len(settings.admin_password.get_secret_value()) < MIN_ADMIN_PASSWORD:
        raise SettingsError(
            f"BLEEP_ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD} characters; "
            "set a longer one in .env"
        )
