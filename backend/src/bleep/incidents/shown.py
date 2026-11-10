"""An incident's stored fixed texts in the person's language, for the API's views and the
notifier (#427).

What is stored stays English, which is what the models read; each of these returns a copy with
the texts that carry a `schemas.shown.Shown` rendered in `language`. Anything stored before #427,
or written by a model or a person, is left as it is.
"""

from bleep import prompts
from bleep.schemas.graph import Decision, Report
from bleep.schemas.incidents import IncidentEvent
from bleep.schemas.shown import Shown


def event_in(event: IncidentEvent, language: str) -> IncidentEvent:
    """A `notice` or an `expected` event with its text in `language`."""
    shown = Shown.in_payload(event.payload)
    if shown is None:
        return event
    payload = {**event.payload, "text": prompts.show(shown, language)}
    return event.model_copy(update={"payload": payload})


def report_in(report: Report, language: str) -> Report:
    """The report with its outcome, fixed cause, decision and timeline in `language`; the
    model's summary, cause and follow-ups are in the language it wrote them in already."""
    fields = {field: prompts.show(shown, language) for field, shown in report.shown.items()}
    timeline = [
        entry.model_copy(update={"text": prompts.show(entry.shown, language)})
        if entry.shown is not None
        else entry
        for entry in report.timeline
    ]
    return report.model_copy(update={**fields, "timeline": timeline})


def decision_in(decision: Decision, language: str) -> Decision:
    """The decision with its text in `language` when the text is Bleep's ("This is expected.")."""
    if decision.shown is None:
        return decision
    return decision.model_copy(update={"text": prompts.show(decision.shown, language)})
