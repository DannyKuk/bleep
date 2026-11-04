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


# Said after a set_env value cut to fit the line; that proposal gets no Approve button.
TOO_LONG = "(value too long to show here: approve on the dashboard)"
LINE_BREAK = "⏎"  # shown in place of a line break or control character inside a step
_BREAKS = re.compile(r"\r\n|[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def steps_text(proposal: Proposal) -> str:
    """One line per step: `1. docker.restart shop-web-1 on demo`.

    A plugin step's params follow it (an MCP tool and its arguments), since the human approves
    those too; they are redacted and cut so that each line stays within `STEP_CHARS` unless its
    head alone is longer, so no step's params can push a later step out of the message. A Docker
    step's params are never shown: its action ignores them, so they could only be model text in
    the approval. A line break or control character inside a step reads `⏎`, so text in a param
    can never pose as another step. A `coolify.set_env` step shows `KEY=value`, redacted
    (`redacted_step`); a value too long for the line keeps its start and end around "…" and is
    followed by `TOO_LONG` (see `shows_in_full`)."""
    return "\n".join(_step_line(n, step)[0] for n, step in enumerate(proposal.steps, 1))


def shows_in_full(proposal: Proposal) -> bool:
    """Whether `steps_text` shows every set_env value whole. When it does not, the proposal is
    approved on the dashboard only (Danny's decision, #38: never approve blind)."""
    return not any(_step_line(n, step)[1] for n, step in enumerate(proposal.steps, 1))


def _one_line(text: str) -> str:
    return _BREAKS.sub(LINE_BREAK, text)


def _step_line(number: int, step: PlanStep) -> tuple[str, bool]:
    """The step's line, and whether a set_env value in it had to be cut."""
    head = _one_line(f"{number}. {step.action} {step.target} on {step.host_id}")
    if not step.params or step.action.startswith("docker."):
        return head, False
    # The head is never cut: a person approves this exact action, target and host. Only the
    # params give way, down to "…" when a long target leaves them no room.
    room = max(STEP_CHARS - len(head) - 3, 1)  # the space and the brackets
    if step.action == "coolify.set_env" and {"key", "value"} <= step.params.keys():
        params = redacted_step(step).params
        key, value = _one_line(params["key"]), _one_line(params["value"])
        if len(key) + 1 + len(value) <= room:
            return f"{head} ({key}={value})", False
        cut = _env_pair(key, value, max(room - len(TOO_LONG) - 1, 1))
        return f"{head} ({cut}) {TOO_LONG}", True
    shown = _one_line(redact_text(", ".join(f"{k}={v}" for k, v in step.params.items())))
    if len(shown) > room:
        shown = shown[: room - 1] + "…"
    return f"{head} ({shown})", False


def _env_pair(key: str, value: str, room: int) -> str:
    """`KEY=value` cut to `room`, the value's middle first, so its start (the scheme and host of a
    URL) and its end both stay in view."""
    keep = room - len(key) - 2  # the "=" and the "…"
    if keep < 2:
        return f"{key}={value}"[: room - 1] + "…"
    start, end = keep - keep // 2, keep // 2
    return f"{key}={value[:start]}…{value[-end:]}"


def confidence_text(diagnosis: Diagnosis | None) -> tuple[str, str, str]:
    """(root cause, category, confidence) as display text; unsure when there is no diagnosis."""
    if diagnosis is None:
        return "not determined", "unknown", "not"
    return diagnosis.root_cause, diagnosis.category, f"{round(diagnosis.confidence * 100)}%"
