"""Memory text on its way in and out: cleaned before it is stored, labelled when a prompt gets it.

A memory record is Bleep's own (its report, the human's decision, a fact), so it reaches the
model as trusted text. What a model wrote into it (the cause above all) can still quote a log
line, so every quoted stretch is wrapped as untrusted on the way out, with the guard's own
wrapper. On the way in, text loses control characters and secrets and has its wrapper tags
escaped, so a stored record can neither leak a secret nor end a wrapper early. A fact or a past
incident's field that copied a wrapper from its prompt (a service name that is not plain, #295) is
wrapped whole on the way out, so the name it holds never comes back as Bleep's own words (#305).
"""

import re

from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.guard.untrusted import escape_untrusted, holds_escaped_tag, wrap_untrusted
from bleep.schemas.graph import NOTE_CHARS, Feedback, PastIncident, ServiceFact

FIELD_CHARS = 500
_FINDING = {"diagnosis": "the diagnosis", "proposal": "the plan"}
QUOTE_SOURCE = "memory.quote"
# The stored title holds the service id, which comes from a container label (#295).
TITLE_SOURCE = "memory.title"
FACT_SOURCE = "memory.fact"
RECORD_SOURCE = "memory.record"  # a past incident's field that copied a wrapper
# A stretch in straight, typographic or back quotes, on one line, of any length. A quote with no
# closing mark (cut short by `clean`, or never closed) runs to the end of the text. Single quotes
# are left alone: they are apostrophes far more often than quotes.
_QUOTED = re.compile(r'"([^"\n]+)(?:"|$)|`([^`\n]+)(?:`|$)|“([^”\n]+)(?:”|$)', re.MULTILINE)


def clean(text: str, limit: int = FIELD_CHARS) -> str:
    """One line of at most `limit` characters, without control characters, secrets or wrapper
    tags. Only the first `4 * limit` characters are read, so the work is bounded."""
    text = " ".join(escape_untrusted(redact_text(strip_control(text[: 4 * limit]))).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def label_quotes(text: str) -> str:
    """`text` with every quoted stretch wrapped as untrusted data, an unclosed one to the end.

    Give it one field at a time, so an unclosed quote ends with its field."""

    def wrap(match: re.Match[str]) -> str:
        quoted = next(group for group in match.groups() if group is not None)
        return wrap_untrusted(QUOTE_SOURCE, quoted)

    return _QUOTED.sub(wrap, text)


def _shown(text: str, source: str) -> str:
    """A stored field as a prompt shows it: wrapped whole when it holds an escaped wrapper tag
    (it copied text a prompt had wrapped), else with its quotes labelled."""
    return wrap_untrusted(source, text) if holds_escaped_tag(text) else label_quotes(text)


def describe_past(past: PastIncident) -> str:
    """A past incident as one line for a prompt: its title wrapped as untrusted, each other
    field wrapped whole when it copied a wrapper (`_shown`), else its quotes labelled."""

    def q(text: str) -> str:
        return _shown(text, RECORD_SOURCE)

    line = (
        f"{past.date:%Y-%m-%d} {wrap_untrusted(TITLE_SOURCE, past.title)}"
        f" (similarity {past.score:.2f}):"
        f" cause {q(past.cause)}; action {q(past.action)}; outcome {q(past.outcome)}"
    )
    if past.human_decision:
        line += f"; the human {q(past.human_decision)}"
    if past.feedback:
        line += f"; feedback: {q(past.feedback)}"
    return line


def describe_fact(fact: ServiceFact) -> str:
    """A service fact as one line for a prompt. A fact that holds an escaped wrapper tag copied
    text a prompt had wrapped, a service name above all, so it is wrapped whole; any other has
    its quotes labelled."""
    origin = "a person" if fact.source == "human" else "a report"
    return f"{_shown(fact.text, FACT_SOURCE)} (from {origin}, {fact.created_at:%Y-%m-%d})"


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
