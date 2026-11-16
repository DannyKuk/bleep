"""Which environment names the `BaseSettings` classes read, so tests can keep a shell's out."""

import os

import pytest
from pydantic import AliasChoices
from pydantic_settings import BaseSettings

from bleep.config import Settings
from bleep.llm.tracing import TracingSettings
from bleep.schemas.settings import ModelSettings

SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (Settings, ModelSettings, TracingSettings)
# Read by the tracing libraries themselves, not by a settings class.
LIBRARY_NAMES = {"langsmith_tracing", "langchain_tracing_v2"}


def names_read(cls: type[BaseSettings]) -> set[str]:
    """Every environment name `cls` reads, lower-cased (pydantic-settings matches any case)."""
    prefix = cls.model_config.get("env_prefix", "")
    names: set[str] = set()
    for field_name, field in cls.model_fields.items():
        if field.alias is not None:
            raise AssertionError(f"{cls.__name__}.{field_name}: `alias=` is not handled here")
        alias = field.validation_alias
        if alias is None:
            names.add(f"{prefix}{field_name}".lower())
        elif isinstance(alias, str):
            names.add(alias.lower())
        elif isinstance(alias, AliasChoices) and all(isinstance(c, str) for c in alias.choices):
            names.update(choice.lower() for choice in alias.choices)
        else:
            raise AssertionError(f"{cls.__name__}.{field_name}: {alias!r} is not handled here")
    return names


def clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    names = LIBRARY_NAMES.union(*(names_read(cls) for cls in SETTINGS_CLASSES))
    for key in [key for key in os.environ if key.lower() in names]:
        monkeypatch.delenv(key)
