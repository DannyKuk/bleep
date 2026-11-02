"""Loads prompt files. Every prompt is a `.md` file under this directory, grouped by area."""

import string
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


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


def load(name: str, /, **variables: str) -> str:
    """Read `PROMPTS_DIR/<name>.md` and fill its placeholders."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt named {name!r}")
    text = path.read_text(encoding="utf-8")
    needed = placeholders(text)
    given = set(variables)
    if needed != given:
        raise PromptError(
            f"prompt {name!r}: missing {sorted(needed - given)}, unused {sorted(given - needed)}"
        )
    return text.format(**variables)
