"""Memory text on its way in and out: cleaned before it is stored, labelled when a prompt gets it.

A memory record is Bleep's own (its report, the human's decision, a fact), so it reaches the
model as trusted text. What a model wrote into it (the cause above all) can still quote a log
line, so every quoted stretch is wrapped as untrusted on the way out, with the guard's own
wrapper. On the way in, text loses control characters and secrets and has its wrapper tags
escaped, so a stored record can neither leak a secret nor end a wrapper early.
"""

import re

from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.guard.untrusted import escape_untrusted, wrap_untrusted
from bleep.schemas.graph import Feedback, PastIncident, ServiceFact

FIELD_CHARS = 500
NOTE_CHARS = 200  # a feedback note, inside a record's one line
_FINDING = {"diagnosis": "the diagnosis", "proposal": "the plan"}
QUOTE_SOURCE = "memory.quote"
# A stretch in straight, typographic or back quotes, on one line. Single quotes are left alone:
# they are apostrophes far more often than quotes.
_QUOTED = re.compile(r'"([^"\n]{1,300})"|`([^`\n]{1,300})`|“([^”\n]{1,300})”')


def clean(text: str, limit: int = FIELD_CHARS) -> str:
    """One line of at most `limit` characters, without control characters, secrets or wrapper
    tags. Only the first `4 * limit` characters are read, so the work is bounded."""
    text = " ".join(escape_untrusted(redact_text(strip_control(text[: 4 * limit]))).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def label_quotes(text: str) -> str:
    """`text` with every quoted stretch wrapped as untrusted data."""

    def wrap(match: re.Match[str]) -> str:
        quoted = next(group for group in match.groups() if group is not None)
        return wrap_untrusted(QUOTE_SOURCE, quoted)

    return _QUOTED.sub(wrap, text)


def describe_past(past: PastIncident) -> str:
    """A past incident as one line for a prompt."""
    line = (
        f"{past.date:%Y-%m-%d} {past.title} (similarity {past.score:.2f}): cause {past.cause};"
        f" action {past.action}; outcome {past.outcome}"
    )
    if past.human_decision:
        line += f"; the human {past.human_decision}"
    if past.feedback:
        line += f"; feedback: {past.feedback}"
    return label_quotes(line)


def describe_fact(fact: ServiceFact) -> str:
    """A service fact as one line for a prompt."""
    origin = "a person" if fact.source == "human" else "a report"
    return label_quotes(f"{fact.text} (from {origin}, {fact.created_at:%Y-%m-%d})")


def describe_feedback(votes: list[Feedback]) -> str | None:
    """The human's votes in words, diagnosis first: "the diagnosis was wrong (it was the disk)
    and the plan was right"; None without votes."""
    ordered = sorted(votes, key=lambda vote: list(_FINDING).index(vote.target))
    parts = [
        f"{_FINDING[vote.target]} was {'right' if vote.vote == 'up' else 'wrong'}"
        + (f" ({clean(vote.note, NOTE_CHARS)})" if vote.note else "")
        for vote in ordered
    ]
    return " and ".join(parts) or None
