from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bleep's own settings, read from `BLEEP_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="BLEEP_", extra="ignore")

    data_dir: Path = Path("/data")
    hosts_file: Path = Path("/config/hosts.yaml")
    admin_password: SecretStr
    secret: SecretStr
