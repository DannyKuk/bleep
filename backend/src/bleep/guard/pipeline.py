"""The one path text takes on its way to the model: bound, clean, redact, escape, cut, wrap."""

from bleep.guard.redact import redact_with_count
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
