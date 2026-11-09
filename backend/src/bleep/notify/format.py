"""Turns the fixed templates in `prompts/notify/` and Bleep's data into Telegram messages.

Messages are sent as MarkdownV2, where an unescaped `.`, `-` or `_` makes Telegram refuse the
whole message. So everything is escaped, the template's own text and every value alike, and the
templates hold plain text. Values are redacted first: a diagnosis is model text that may echo a
log line. The only markup is bold, which `bold_first_line` adds to the line that says what kind of
message it is (#380).
"""

import re
import unicodedata
from datetime import UTC, datetime, timedelta, tzinfo

from bleep import prompts
from bleep.guard.plan_check import MAX_STEPS
from bleep.guard.redact import params_line, redact_text, redacted_step, step_head
from bleep.guard.sanitize import INVISIBLE_CHARS, PRESENTATION_SELECTORS
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
    return _fit("".join(out))


def _fit(text: str) -> str:
    if len(text) > TELEGRAM_LIMIT:
        text = text[: TELEGRAM_LIMIT - 1]
        # Never end on a lone backslash: it would escape nothing and Telegram would refuse it.
        text = text[: len(text) - (len(text) - len(text.rstrip("\\"))) % 2] + "…"
    return text


def bold_first_line(rendered: str) -> str:
    """`rendered` (escaped by `render`) with its first line in bold."""
    head, _, rest = rendered.partition("\n")
    return f"*{head}*" + ("\n" + rest if rest else "")


def message(*parts: str) -> str:
    """Rendered parts as one message, one under the other, cut to Telegram's limit."""
    return _fit("\n".join(part for part in parts if part))


def duration(span: timedelta) -> str:
    """A span as a person says it in a chat: "4 min", "2 h", "3 days"."""
    minutes = max(int(span.total_seconds() // 60), 1)
    if minutes < 60:
        return phrase("duration_min", n=str(minutes))
    if minutes < 48 * 60:
        return phrase("duration_h", n=str(minutes // 60))
    return phrase("duration_days", n=str(minutes // (24 * 60)))


def when(at: datetime, now: datetime, zone: tzinfo | None) -> str:
    """A time as a person says it in a chat, in `zone`: "14:30" today, "tomorrow 08:00", else
    the weekday ("Thu 08:00"), as the dashboard's quiet pill says it. Without a zone (none was
    configured) it is UTC and says so: "14:30 UTC"."""
    if zone is None:
        return phrase("when_utc", when=when(at, now, UTC))
    local, today = at.astimezone(zone), now.astimezone(zone).date()
    time = local.strftime("%H:%M")
    if local.date() == today:
        return time
    if local.date() == today + timedelta(days=1):
        return phrase("when_tomorrow", time=time)
    return phrase("when_day", day=local.strftime("%a"), time=time)


def answer(name: str, **values: str) -> str:
    """A short plain-text answer (a pop-up), not escaped."""
    return prompts.load(f"notify/{name}", **values).strip()


def phrase(name: str, **values: str) -> str:
    """A few words that go into a message as a value (`render` escapes them there)."""
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


# Whitespace, and letters and marks that render blank (Braille blank, the Hangul fillers, the
# Khmer inherent vowels, the combining grapheme joiner): a run of either could pad a line.
_INVISIBLE = re.compile(INVISIBLE_CHARS)
_FILLERS = re.compile(r"[\u034f\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0]")
# Common after an emoji, and a single one cannot hide a name, so a step line drops them instead of
# marking them; the plan check refuses a step that holds one (`guard/redact.hides_params`).
_PRESENTATION = re.compile(f"[{PRESENTATION_SELECTORS}]")
_SPACES = re.compile(r"\s+")


def prose_line(text: str) -> Redacted:
    """Model or event text that sits next to the plan (a title, the root cause, the reason) as one
    redacted line: breaks read `⏎`, format characters `�`, and a run of blanks is one space. So
    it can never start a line of its own that reads like a plan step, not even by padding with
    spaces until the chat wraps it there."""
    return Redacted(_SPACES.sub(" ", _one_line(_FILLERS.sub(" ", redact_text(text.strip())))))


def _one_line(text: str) -> str:
    """`text` on one visible line: breaks and control characters read `⏎`, format characters
    (Unicode category Cf: bidi overrides and isolates, zero-width marks) and the other characters
    that draw nothing (`INVISIBLE_CHARS`: the grapheme joiner, fillers, variation selectors) read
    `�`; U+FE0E and U+FE0F are dropped."""
    text = _BREAKS.sub(LINE_BREAK, text)
    text = _PRESENTATION.sub("", text)
    hidden = (c for c in text if unicodedata.category(c) == "Cf" or _INVISIBLE.fullmatch(c))
    return text.translate({ord(c): HIDDEN_FORMAT for c in hidden})


def _step_line(number: int, step: PlanStep) -> tuple[str, bool]:
    """The step's line, and whether its params had to be cut to fit it."""
    head = _one_line(redact_text(step_head(number, step)))
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
    shown = _one_line(redact_text(params_line(step)))
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
