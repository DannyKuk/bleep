"""Turns the fixed templates in `prompts/notify/` and Bleep's data into Telegram messages.

Messages are sent as MarkdownV2, where an unescaped `.`, `-` or `_` makes Telegram refuse the
whole message. So everything is escaped, the template's own text and every value alike, and the
templates hold plain text. Values are redacted first: a diagnosis is model text that may echo a
log line.
"""

import re

from bleep import prompts
from bleep.guard.redact import redact_text
from bleep.schemas.graph import Diagnosis, Proposal

TELEGRAM_LIMIT = 4096
FIELD_CHARS = 600  # one value; keeps a message far below the limit
_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_MARK = re.compile(r"\x00(\w+)\x00")


def escape(text: str) -> str:
    """Escape every MarkdownV2 special character."""
    return _SPECIAL.sub(r"\\\1", text)


def _cap(text: str) -> str:
    text = redact_text(text.strip())
    return text if len(text) <= FIELD_CHARS else text[: FIELD_CHARS - 1] + "…"


def render(name: str, **values: str) -> str:
    """The `notify/<name>` template, filled and escaped, ready for `parse_mode=MarkdownV2`."""
    # Each value goes in as a marker the template's own text cannot contain; the text between
    # markers is escaped as the template's, the values as values.
    marked = prompts.load(f"notify/{name}", **{key: f"\x00{key}\x00" for key in values}).strip()
    out, last = [], 0
    for match in _MARK.finditer(marked):
        out.append(escape(marked[last : match.start()]))
        out.append(escape(_cap(values[match.group(1)])))
        last = match.end()
    out.append(escape(marked[last:]))
    text = "".join(out)
    if len(text) > TELEGRAM_LIMIT:
        text = text[: TELEGRAM_LIMIT - 1]
        # Never end on a lone backslash: it would escape nothing and Telegram would refuse it.
        text = text[: len(text) - (len(text) - len(text.rstrip("\\"))) % 2] + "…"
    return text


def answer(name: str, **values: str) -> str:
    """A short plain-text answer (a pop-up), not escaped."""
    return prompts.load(f"notify/{name}", **values).strip()


def steps_text(proposal: Proposal) -> str:
    """One line per step: `1. docker.restart shop-web-1 on demo`, with its params, a set_env
    value hidden (`PlanStep.text`)."""
    return "\n".join(f"{n}. {step.text()}" for n, step in enumerate(proposal.steps, 1))


def confidence_text(diagnosis: Diagnosis | None) -> tuple[str, str, str]:
    """(root cause, category, confidence) as display text; unsure when there is no diagnosis."""
    if diagnosis is None:
        return "not determined", "unknown", "not"
    return diagnosis.root_cause, diagnosis.category, f"{round(diagnosis.confidence * 100)}%"
