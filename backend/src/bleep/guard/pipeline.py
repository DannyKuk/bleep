"""The one path text takes on its way to the model: bound, clean, redact, escape, cut, wrap."""

import re

from bleep.guard.redact import redact_text, redact_with_count
from bleep.guard.sanitize import (
    MAX_CHARS,
    collapse_repeats,
    cut_tail,
    normalise_newlines,
    strip_control,
    tail_lines,
)
from bleep.guard.untrusted import escape_untrusted, wrap_untrusted

WORK_FACTOR = 64  # the raw text is cut to this many times `max_chars` before anything else
NAME_CHARS = 200
# The characters of Docker container names, Compose projects and Bleep's service ids: no space,
# quote or bracket, so a name made of them cannot hold a sentence or a tag.
_PLAIN_NAME = re.compile(rf"[A-Za-z0-9][A-Za-z0-9_.:/-]{{0,{NAME_CHARS - 1}}}")


def guard_text(source: str, text: str | bytes, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """Return the wrapped text for the model and the number of secrets redacted.

    1. Normalise newlines (so `\r` cannot multiply lines), then keep only the last
       64 x `max_chars` characters, cut at a line boundary. Everything after works on a bounded
       input whatever the container printed.
    2. Strip escape codes, control and invisible characters, so nothing splits a secret or a tag.
    3. Redact: lines over 4,000 characters are cut (after redacting around the cut), the rest is
       redacted on the whole text before any further cut, so a secret on the cut point cannot
       leak half of itself.
    4. Collapse repeated lines, keep the tail, then escape tag look-alikes and keep the tail
       again, so the result is at most `max_chars` plus the cut note and the wrapper.
    5. Wrap with a random id.
    """
    bounded, dropped = tail_lines(normalise_newlines(text), WORK_FACTOR * max_chars)
    redacted, count = redact_with_count(strip_control(bounded))
    collapsed = collapse_repeats(redacted)
    kept = collapsed[-max_chars:]
    escaped = escape_untrusted(kept)
    # N counts original characters: what was dropped up front and by each cut, minus the growth
    # the escaping added to the part the second cut drops.
    extra = max(0, len(escaped) - max_chars)
    growth = escaped[:extra].count("&lt;") * 3
    cut = dropped + len(collapsed) - len(kept) + extra - growth
    return wrap_untrusted(source, cut_tail(escaped, max_chars, cut - extra)), count


def is_plain_name(name: str) -> bool:
    """Whether a name from a host may stand as it is in Bleep's own text: letters, digits and
    `_.:/-` only, at most 200 characters, and nothing in it that redaction would hide."""
    return bool(_PLAIN_NAME.fullmatch(name)) and redact_text(name) == name


def guard_name(source: str, name: str) -> str:
    """A name from a host (a service name taken from a label) as a prompt may show it: a plain
    name as it is, any other through `guard_text`, capped at 200 characters and wrapped."""
    return name if is_plain_name(name) else guard_text(source, name, NAME_CHARS)[0]
