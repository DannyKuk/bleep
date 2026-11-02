"""The one path text takes on its way to the model: clean, redact, escape, cut, wrap."""

from bleep.guard.redact import redact_with_count
from bleep.guard.sanitize import MAX_CHARS, cap_lines, collapse_repeats, cut_tail, strip_control
from bleep.guard.untrusted import escape_untrusted, wrap_untrusted


def guard_text(source: str, text: str | bytes, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """Return the wrapped text for the model and the number of secrets redacted.

    1. Strip escape codes, control and invisible characters, so nothing splits a secret or a tag.
    2. Cap each line at 4,000 characters, so no pattern can be made slow by one huge line.
    3. Redact on the whole text before any cut, so a secret on the cut point cannot leak half.
    4. Collapse repeated lines and escape tag look-alikes.
    5. Keep the tail, so the result is at most `max_chars` plus the cut note and the wrapper.
    6. Wrap with a random id.
    """
    capped = cap_lines(strip_control(text))
    redacted, count = redact_with_count(capped)
    escaped = escape_untrusted(collapse_repeats(redacted))
    return wrap_untrusted(source, cut_tail(escaped, max_chars)), count
