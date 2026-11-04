"""Turns the fixed templates in `prompts/notify/` and Bleep's data into Telegram messages.

Messages are sent as MarkdownV2, where an unescaped `.`, `-` or `_` makes Telegram refuse the
whole message. So everything is escaped, the template's own text and every value alike, and the
templates hold plain text. Values are redacted first: a diagnosis is model text that may echo a
log line.
"""

import re

from bleep import prompts
from bleep.guard.plan_check import MAX_STEPS
from bleep.guard.redact import redact_text, redacted_step
from bleep.schemas.graph import Diagnosis, PlanStep, Proposal

TELEGRAM_LIMIT = 4096
FIELD_CHARS = 600  # one value; keeps a message far below the limit
# One step of a plan; with at most MAX_STEPS steps the list stays within FIELD_CHARS.
STEP_CHARS = (FIELD_CHARS - MAX_STEPS) // MAX_STEPS
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
    """One line per step: `1. docker.restart shop-web-1 on demo`.

    A plugin step's params follow it (an MCP tool and its arguments), since the human approves
    those too; they are redacted and cut so that each line stays within `STEP_CHARS` unless its
    head alone is longer, so no step's params can push a later step out of the message. A Docker
    step's params are never shown: its action ignores them, so they could only be model text in
    the approval. A `coolify.set_env` step shows `KEY=value`, redacted (`redacted_step`); a value
    too long for the line keeps its start and end around "…", so the key and the value always
    show."""
    return "\n".join(_step_line(n, step) for n, step in enumerate(proposal.steps, 1))


def _step_line(number: int, step: PlanStep) -> str:
    head = f"{number}. {step.action} {step.target} on {step.host_id}"
    if not step.params or step.action.startswith("docker."):
        return head
    # The head is never cut: a person approves this exact action, target and host. Only the
    # params give way, down to "…" when a long target leaves them no room.
    room = max(STEP_CHARS - len(head) - 3, 1)  # the space and the brackets
    if step.action == "coolify.set_env" and {"key", "value"} <= step.params.keys():
        params = redacted_step(step).params
        return f"{head} ({_env_pair(params['key'], params['value'], room)})"
    shown = redact_text(", ".join(f"{key}={value}" for key, value in step.params.items()))
    if len(shown) > room:
        shown = shown[: room - 1] + "…"
    return f"{head} ({shown})"


def _env_pair(key: str, value: str, room: int) -> str:
    """`KEY=value` within `room`, the value's middle cut first, so its start (the scheme and
    host of a URL) and its end both stay in view."""
    pair = f"{key}={value}"
    if len(pair) <= room:
        return pair
    keep = room - len(key) - 2  # the "=" and the "…"
    if keep < 2:
        return pair[: room - 1] + "…"
    start, end = keep - keep // 2, keep // 2
    return f"{key}={value[:start]}…{value[-end:]}"


def confidence_text(diagnosis: Diagnosis | None) -> tuple[str, str, str]:
    """(root cause, category, confidence) as display text; unsure when there is no diagnosis."""
    if diagnosis is None:
        return "not determined", "unknown", "not"
    return diagnosis.root_cause, diagnosis.category, f"{round(diagnosis.confidence * 100)}%"
