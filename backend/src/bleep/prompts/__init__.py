"""Loads prompt files. Every prompt is a `.md` file under this directory, grouped by area.

The dev panel can stand an override in for a file (`use_overrides`); `load` reads the override
when there is one and the file otherwise, and fills both with the same checks.
"""

import hashlib
import json
import re
import string
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

PROMPTS_DIR = Path(__file__).parent
NAME = re.compile(r"^[a-z0-9_]+(?:/[a-z0-9_]+)*$")
# The prompts the dev panel may override, by name: a new prompt file is not one until it is named
# here, and `tests/test_prompts.py` fails until every file is named here or below.
OVERRIDABLE = frozenset(
    {
        "graph/diagnose",
        "graph/diagnose_de",
        "graph/diagnose_missing",
        "graph/investigate",
        "graph/investigate_again",
        "graph/investigate_mcp",
        "graph/look_diagnose",
        "graph/look_investigate",
        "graph/look_report",
        "graph/propose",
        "graph/propose_de",
        "graph/propose_retry",
        "graph/propose_unsure",
        "graph/report",
        "graph/report_de",
        "graph/report_facts",
        "graph/url_checks",
        "graph/url_checks_diagnose",
        "graph/verify_failed",
        "llm/json_fallback",
        "llm/json_retry",
        "watcher/recovery_line",
        "watcher/recovery_line_de",
    }
)
# Never overridable. The eval prompts are loaded by the eval runner, a process of its own that never
# reads overrides. The Telegram texts are what the approver reads before saying yes. `untrusted/`
# holds the untrusted-data rule of each prompt that has one, which the code fills into that
# prompt's `{untrusted_rule}`, so an override changes only the rest; Diagnose's `planted_in` field
# is there too, filled into `{planted_in_rule}`. `graph/system` holds the
# untrusted-data rule with no placeholder to check an override against. `graph/propose_mcp` tells
# the model how to write an `mcp.call` step around the server's wrapped tool schemas, and
# `graph/verify_unproven` tells it not to start again a container a human approved stopping.
# `talk/` reads what the person writes and holds the fixed lines that refuse it (#401). `shown/`
# holds the fixed texts other parts write for the person, stored in English (#427).
NOT_OVERRIDABLE_AREAS = ("evals/", "notify/", "shown/", "talk/", "untrusted/")
NOT_OVERRIDABLE = frozenset({"graph/propose_mcp", "graph/system", "graph/verify_unproven"})
# The languages a person may choose (#373). English is the files as they are; each other language
# has a twin of every text a person reads, at `<area>/<language>/<name>.md` (#375).
LANGUAGES = ("en", "de")
# The texts a person reads are Telegram's, the chat's and the fixed texts the other parts write
# (`shown/`, #427). In those areas the model reads `talk/read` and `talk/message`, which stay
# English as every prompt does, and the talk log's export (`talk/log_*`) is a transcript for
# reviewing answers, kept English too. A `shown/` text is stored in English, which is what a
# model reads of it, and only shown in another language (`show`).
TRANSLATED_AREAS = ("notify/", "shown/", "talk/")
STAYS_ENGLISH = frozenset({"talk/read", "talk/message"})
STAYS_ENGLISH_PREFIXES = ("talk/log_",)
# C0 and C1 controls but tab and line breaks; the bidi overrides, isolates and marks (U+200E,
# U+200F, U+061C), which reorder what a reader of the panel sees without changing what the model
# reads; and the zero-width space and the byte-order mark, which hide where text is split.
_CONTROL = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u061c\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)

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
    return [name for name in names if name in OVERRIDABLE]


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
        raise PromptError("control characters, bidi marks and invisible characters are not allowed")
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


def text_version(text: str | None) -> str:
    """A short hash of an override's text, `None` standing for no override: the dev panel's
    version of a prompt, and what an incident records of each override it ran with."""
    return hashlib.sha256(json.dumps({"text": text}).encode()).hexdigest()[:16]


def override_versions() -> dict[str, str]:
    """The overrides in force, as prompt name to `text_version` of the text; never the text."""
    return {name: text_version(text) for name, text in sorted(_overrides.items())}


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


def _twin_of(name: str) -> str | None:
    """The English text `name` is a twin of (`notify/de/x` of `notify/x`), else None."""
    area, _, rest = name.partition("/")
    language, _, base = rest.partition("/")
    return f"{area}/{base}" if language in LANGUAGES[1:] and base else None


def translated(name: str) -> bool:
    """Whether the English text `name` is read by a person, so it has a twin in each language."""
    return (
        name.startswith(TRANSLATED_AREAS)
        and _twin_of(name) is None
        and name not in STAYS_ENGLISH
        and not name.startswith(STAYS_ENGLISH_PREFIXES)
    )


def in_language(name: str, language: str) -> str:
    """The name of the text `name` in `language`: itself in English, else its twin."""
    if language not in LANGUAGES:
        raise PromptError(f"no language {language!r}; known: {', '.join(LANGUAGES)}")
    if language == LANGUAGES[0]:
        return name
    if not translated(name):
        raise PromptError(f"{name!r} stays English")
    area, _, rest = name.partition("/")
    return f"{area}/{language}/{rest}"


def load_in(language: str, name: str, /, **variables: str) -> str:
    """`load` the text `name` in `language`."""
    return load(in_language(name, language), **variables)


class ShownText(Protocol):
    """A text Bleep's code wrote for a person (`schemas.shown.Shown`)."""

    @property
    def name(self) -> str: ...

    @property
    def values(self) -> Mapping[str, str | ShownText]: ...

    @property
    def parts(self) -> Sequence[ShownText]: ...

    @property
    def sep(self) -> str: ...


def show(text: ShownText, language: str) -> str:
    """`text` in `language`: each of its `shown/` templates filled, its parts joined. In English
    it is the text as it is stored and as a model reads it."""
    if not text.name:
        return text.sep.join(show(part, language) for part in text.parts)
    values = {
        key: value if isinstance(value, str) else show(value, language)
        for key, value in text.values.items()
    }
    return load_in(language, f"shown/{text.name}", **values).strip()


def twin_problems() -> list[str]:
    """What keeps the texts a person reads from being the same in every language: a text with
    no twin, a twin with no English text or of one that stays English, or a twin whose
    placeholders differ from the English text's."""
    names = {p.relative_to(PROMPTS_DIR).with_suffix("").as_posix() for p in all_prompt_files()}
    problems = []
    for name in sorted(names):
        english = _twin_of(name)
        if english is None:
            if translated(name):
                problems += [
                    f"{name} has no {language} twin"
                    for language in LANGUAGES[1:]
                    if in_language(name, language) not in names
                ]
        elif not translated(english):
            problems.append(f"{name} is a twin of a text that stays English")
        elif english not in names:
            problems.append(f"{name} has no English file")
        else:
            theirs, ours = placeholders(file_text(name)), placeholders(file_text(english))
            if theirs != ours:
                problems.append(f"{name} has {_listed(theirs)}, {english} has {_listed(ours)}")
    return problems


def _listed(names: set[str]) -> str:
    return ", ".join(f"{{{name}}}" for name in sorted(names)) or "none"
