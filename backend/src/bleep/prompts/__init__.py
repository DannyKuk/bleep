"""Loads prompt files. Every prompt is a `.md` file under this directory, grouped by area.

The dev panel can stand an override in for a file (`use_overrides`); `load` reads the override
when there is one and the file otherwise, and fills both with the same checks.
"""

import re
import string
from collections.abc import Mapping
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
NAME = re.compile(r"^[a-z0-9_]+(?:/[a-z0-9_]+)*$")
# The eval prompts are loaded by the eval runner, a process of its own that never reads overrides.
# The Telegram texts are what the approver reads before saying yes, and `graph/system` holds the
# untrusted-data rule with no placeholder to check an override against.
NOT_OVERRIDABLE_AREAS = ("evals/", "notify/")
NOT_OVERRIDABLE = frozenset({"graph/system"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_overrides: dict[str, str] = {}


class PromptError(Exception):
    """A prompt is missing, malformed, or filled with the wrong variables."""


def placeholders(text: str) -> set[str]:
    """The `{name}` placeholders in a prompt. `{{` and `}}` are literal braces."""
    names: set[str] = set()
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as error:
        raise PromptError(f"bad braces: {error}") from error
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if not field.isidentifier() or spec or conversion:
            raise PromptError(f"only plain {{name}} placeholders are allowed, got {{{field}}}")
        names.add(field)
    return names


def all_prompt_files() -> list[Path]:
    return sorted(p for p in PROMPTS_DIR.rglob("*.md") if p.name != "README.md")


def overridable_names() -> list[str]:
    """Every prompt the dev panel may override, as `area/name`."""
    names = (p.relative_to(PROMPTS_DIR).with_suffix("").as_posix() for p in all_prompt_files())
    return [
        name
        for name in names
        if not name.startswith(NOT_OVERRIDABLE_AREAS) and name not in NOT_OVERRIDABLE
    ]


def file_text(name: str) -> str:
    """The text of `PROMPTS_DIR/<name>.md`, ignoring any override."""
    path = PROMPTS_DIR / f"{name}.md"
    if NAME.match(name) is None or path.name.lower() == "readme.md" or not path.is_file():
        raise PromptError(f"no prompt named {name!r}")
    return path.read_text(encoding="utf-8")


def check_override(name: str, text: str) -> None:
    """Raise `PromptError` unless `text` can stand in for the prompt `name`: it must parse and
    use exactly the file's placeholders, since the callers fill exactly those."""
    if name not in overridable_names():
        raise PromptError(f"no prompt named {name!r} can be overridden")
    if _CONTROL.search(text):
        raise PromptError("control characters are not allowed")
    needed, given = placeholders(file_text(name)), placeholders(text)
    if needed != given:
        missing = ", ".join(f"{{{p}}}" for p in sorted(needed - given)) or "none"
        unknown = ", ".join(f"{{{p}}}" for p in sorted(given - needed)) or "none"
        raise PromptError(
            f"use exactly the file's placeholders; missing: {missing}; unknown: {unknown}"
        )


def use_overrides(overrides: Mapping[str, str]) -> None:
    """Make `overrides` (prompt name to text, each passed by `check_override`) the texts that
    stand in for the files; `{}` puts every file back."""
    global _overrides
    _overrides = dict(overrides)  # swapped whole, so a load never sees half an update


def load(name: str, /, **variables: str) -> str:
    """Read the override for `name`, else `PROMPTS_DIR/<name>.md`, and fill its placeholders."""
    text = _overrides.get(name)
    if text is None:
        text = file_text(name)
    needed = placeholders(text)
    given = set(variables)
    if needed != given:
        raise PromptError(
            f"prompt {name!r}: missing {sorted(needed - given)}, unused {sorted(given - needed)}"
        )
    return text.format(**variables)
