"""The one path text takes on its way to the model: clean, redact, cut, wrap."""

from bleep.guard.redact import redact_with_count
from bleep.guard.sanitize import collapse_repeats, sanitize, strip_control
from bleep.guard.untrusted import wrap_untrusted


def guard_text(source: str, text: str | bytes, max_chars: int = 8000) -> tuple[str, int]:
    """Return the wrapped text for the model and the number of secrets redacted.

    Escape codes are stripped first so they cannot split a secret and hide it from the patterns.
    Redaction runs on the whole text before the cut, so a secret on the cut point cannot leak half
    of itself. Then repeated lines are collapsed, the tail is kept, and the result is wrapped.
    """
    cleaned = collapse_repeats(strip_control(text))
    redacted, count = redact_with_count(cleaned)
    return wrap_untrusted(source, sanitize(redacted, max_chars)), count
