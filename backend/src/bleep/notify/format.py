"""Turns the fixed templates in `prompts/notify/` and Bleep's data into Telegram messages.

Messages are sent as MarkdownV2, where an unescaped `.`, `-` or `_` makes Telegram refuse the
whole message. So everything is escaped, the template's own text and every value alike, and the
templates hold plain text. Values are redacted first: a diagnosis is model text that may echo a
log line.
"""

import re
import unicodedata

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


class Redacted(str):
    """Text already redacted for display, such as `steps_text`: `render` shows it as it is,
    since redacting `KEY=value` again would hide a value the approver must see."""


def _cap(text: str) -> str:
    text = text.strip() if isinstance(text, Redacted) else redact_text(text.strip())
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


# Said after a set_env value, or a plugin step's params, cut to fit the line; that proposal gets
# no Approve button.
TOO_LONG = "(value too long to show here: approve on the dashboard)"
PARAMS_TOO_LONG = "(too long to show here: approve on the dashboard)"
LINE_BREAK = "⏎"  # shown in place of a line break or control character inside a step
HIDDEN_FORMAT = "�"  # shown in place of an invisible format character (bidi, zero-width)
_BREAKS = re.compile(r"\r\n|[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def steps_text(proposal: Proposal) -> Redacted:
    """One line per step: `1. docker.restart shop-web-1 on demo`.

    A plugin step's params follow it (an MCP `tool` first, then its arguments), since the human
    approves those too; they are redacted, and when they do not fit the line within `STEP_CHARS`
    they are cut and followed by `PARAMS_TOO_LONG`, also when a long head leaves them no room
    (`(…)`). A Docker step's params are never shown: its action ignores them, so they could only
    be model text in the approval. A line break or control character inside a step reads `⏎` and
    a format character `�`, so text in a param can never pose as another step or reorder what is
    shown. A `coolify.set_env` step shows `KEY=value`, redacted (`redacted_step`); a value too
    long for the line keeps its start and end around "…" and is followed by `TOO_LONG`. A plan
    with any cut gets no Approve button (`shows_in_full`)."""
    return Redacted("\n".join(_step_line(n, step)[0] for n, step in enumerate(proposal.steps, 1)))


def shows_in_full(proposal: Proposal) -> bool:
    """Whether the message shows every step whole: no set_env value or plugin param was cut, and
    the steps fit the field `render` caps them at (long heads can overflow it and lose a later
    step). When not, the proposal is approved on the dashboard only (Danny's decision, #38: never
    approve blind)."""
    if any(_step_line(n, step)[1] for n, step in enumerate(proposal.steps, 1)):
        return False
    return len(steps_text(proposal).strip()) <= FIELD_CHARS


def _one_line(text: str) -> str:
    """`text` on one visible line: breaks and control characters read `⏎`, format characters
    (Unicode category Cf: bidi overrides and isolates, zero-width marks) read `�`."""
    text = _BREAKS.sub(LINE_BREAK, text)
    return "".join(HIDDEN_FORMAT if unicodedata.category(c) == "Cf" else c for c in text)


def _step_line(number: int, step: PlanStep) -> tuple[str, bool]:
    """The step's line, and whether its params had to be cut to fit it."""
    head = _one_line(redact_text(f"{number}. {step.action} {step.target} on {step.host_id}"))
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
    # The tool goes first: planted text could otherwise put long arguments before it and push
    # the name of what runs past the cut.
    params = sorted(step.params.items(), key=lambda item: item[0] != "tool")
    shown = _one_line(redact_text(", ".join(f"{k}={v}" for k, v in params)))
    if len(shown) <= room:
        return f"{head} ({shown})", False
    keep = max(room - len(PARAMS_TOO_LONG) - 1, 1)
    return f"{head} ({shown[: keep - 1]}…) {PARAMS_TOO_LONG}", True


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
