from dataclasses import dataclass

from bleep.config import Settings


@dataclass
class AppParts:
    """Everything the app runs on, built once from the settings."""


def build_parts(settings: Settings) -> AppParts:
    return AppParts()
