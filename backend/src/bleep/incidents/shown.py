"""An incident's stored fixed texts in the person's language, for the API's views and the
notifier (#427).

What is stored stays English, which is what the models read; each of these returns a copy with
the texts that carry a `schemas.shown.Shown` rendered in `language`. Anything stored before #427,
or written by a model or a person, is left as it is. A text that no longer renders (its template
was renamed or its values no longer fit) keeps the English stored next to it, so one drifted row
never takes a view, the event stream or an end message down.
"""

import logging

from pydantic import ValidationError

from bleep import prompts
from bleep.graph.nodes.report import ENTRY_CHARS, clip
from bleep.schemas.graph import Decision, Report
from bleep.schemas.incidents import IncidentEvent
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)


def _rendered(shown: Shown, language: str, stored: str) -> str:
    try:
        return prompts.show(shown, language)
    except prompts.PromptError as error:
        log.warning("shown text %r does not render, keeping the stored English: %s", shown, error)
        return stored


def event_in(event: IncidentEvent, language: str) -> IncidentEvent:
    """A `notice` or an `expected` event with its text in `language`."""
    try:
        shown = Shown.in_payload(event.payload)
    except ValidationError as error:
        log.warning(
            "event %s/%s keeps a shown text that is not one: %s",
            event.incident_id,
            event.seq,
            error,
        )
        return event
    if shown is None:
        return event
    text = _rendered(shown, language, str(event.payload.get("text", "")))
    return event.model_copy(update={"payload": {**event.payload, "text": text}})


def report_in(report: Report, language: str) -> Report:
    """The report with its outcome, fixed cause, decision and timeline in `language`; the
    model's summary, cause and follow-ups are in the language it wrote them in already. A
    timeline line is cut to the length it has in English, which a longer translation would pass."""
    fields = {
        field: _rendered(shown, language, getattr(report, field) or "")
        for field, shown in report.shown.items()
    }
    timeline = [
        entry.model_copy(
            update={"text": clip(_rendered(entry.shown, language, entry.text), ENTRY_CHARS)}
        )
        if entry.shown is not None
        else entry
        for entry in report.timeline
    ]
    return report.model_copy(update={**fields, "timeline": timeline})


def decision_in(decision: Decision, language: str) -> Decision:
    """The decision with its text in `language` when the text is Bleep's ("This is expected.")."""
    if decision.shown is None:
        return decision
    text = _rendered(decision.shown, language, decision.text or "")
    return decision.model_copy(update={"text": text})
