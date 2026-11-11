"""An incident's stored fixed texts in the person's language, for the API's views and the
notifier (#427).

What is stored stays English, which is what the models read; each of these returns a copy with
the texts that carry a `schemas.shown.Shown` rendered in `language`. Anything stored before #427,
or written by a model or a person, is left as it is. A text that no longer renders (its template
was renamed or its values no longer fit) keeps the English stored next to it, so one drifted row
never takes a view, the event stream or an end message down.
"""

import logging
from typing import Any

from pydantic import ValidationError

from bleep import prompts
from bleep.graph.nodes.report import ENTRY_CHARS, clip
from bleep.schemas.graph import Check, Decision, Diagnosis, Proposal, Report, VerificationResult
from bleep.schemas.incidents import IncidentEvent, RecoveryEvent
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)


def _rendered(shown: Shown, language: str, stored: str) -> str:
    try:
        return prompts.show(shown, language)
    except prompts.PromptError as error:
        log.warning("shown text %r does not render, keeping the stored English: %s", shown, error)
        return stored


def _as_stored(shown: Shown | None, language: str, stored: str) -> str:
    """`shown` in `language` while its English is still `stored`, word for word; a text changed
    after it was made (cut, redacted, joined) shows as it is stored (#455)."""
    if shown is None or _rendered(shown, prompts.LANGUAGES[0], "") != stored:
        return stored
    return _rendered(shown, language, stored)


def proposal_in(proposal: Proposal, language: str) -> Proposal:
    """The plan with Bleep's own reason, refusal reasons and instructions in `language`; the
    model's reason and instructions are as it wrote them."""
    fields = {
        field: _as_stored(shown, language, getattr(proposal, field) or "")
        for field, shown in proposal.shown.items()
    }
    reasons = list(proposal.refusal_reasons)
    if len(proposal.refusal_reasons_shown) == len(reasons):
        reasons = [
            _as_stored(shown, language, stored)
            for shown, stored in zip(proposal.refusal_reasons_shown, reasons, strict=True)
        ]
    update = {**fields, "refusal_reasons": reasons, "shown": {}, "refusal_reasons_shown": []}
    return proposal.model_copy(update=update)


def diagnosis_in(diagnosis: Diagnosis, language: str) -> Diagnosis:
    """The diagnosis with Bleep's own cause and evidence lines in `language`."""
    evidence = [
        item.model_copy(
            update={"detail": _as_stored(item.shown, language, item.detail), "shown": None}
        )
        for item in diagnosis.evidence
    ]
    cause = _as_stored(diagnosis.shown, language, diagnosis.root_cause)
    return diagnosis.model_copy(update={"root_cause": cause, "evidence": evidence, "shown": None})


def recovery_in(event: RecoveryEvent, language: str) -> RecoveryEvent:
    """The recovery with Bleep's own line in `language`; the cheap model's is as it wrote it."""
    explanation = _as_stored(event.explanation_shown, language, event.explanation)
    return event.model_copy(update={"explanation": explanation, "explanation_shown": None})


def event_in(event: IncidentEvent, language: str) -> IncidentEvent:
    """A `notice` or an `expected` event with its text in `language`, and a `verification`
    event with each check's target and detail."""
    if event.kind == "verification":
        return _verification_event_in(event, language)
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


def _verification_event_in(event: IncidentEvent, language: str) -> IncidentEvent:
    """The event with each check's target and detail in `language`; a check stored before #454, or
    one that is not a check, is left as it is."""
    checks = []
    for raw in event.payload.get("checks") or []:
        try:
            check = check_in(Check.model_validate(raw), language)
        except ValidationError:
            checks.append(raw)
            continue
        checks.append({**raw, "target": check.target, "detail": check.detail})
    if not checks:
        return event
    return event.model_copy(update={"payload": {**event.payload, "checks": checks}})


def check_in(check: Check, language: str) -> Check:
    """The check with its target and detail in `language`, no longer a shown text."""
    target, detail = check.target, check.detail
    if check.target_shown is not None:
        target = _rendered(check.target_shown, language, target)
    if check.shown is not None:
        detail = _rendered(check.shown, language, detail)
    return check.model_copy(
        update={"target": target, "detail": detail, "target_shown": None, "shown": None}
    )


def verification_in(result: VerificationResult, language: str) -> VerificationResult:
    """The result with each check in `language`; its `lines()` are then the chat's lines."""
    return result.model_copy(update={"checks": [check_in(c, language) for c in result.checks]})


def report_in(report: Report, language: str) -> Report:
    """The report with its outcome, fallback summary, fixed cause, decision, timeline, steps and
    planted-orders note in `language`; the model's summary, cause and follow-ups are in the
    language it wrote them in already. A timeline line is cut to the length it has in English,
    which a longer translation would pass."""
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
    steps = [
        step.model_copy(update={"text": _rendered(step.shown, language, step.text)})
        if step.shown is not None
        else step
        for step in report.steps
    ]
    follow_ups = list(report.follow_ups)
    for place, shown in report.shown_follow_ups.items():
        if 0 <= place < len(follow_ups):
            follow_ups[place] = _rendered(shown, language, follow_ups[place])
    update: dict[str, Any] = {**fields, "timeline": timeline, "follow_ups": follow_ups}
    stored = "; ".join(step.text for step in report.steps)
    if steps and report.action_taken == stored:  # a cut action text is shown as it was cut
        update |= {"steps": steps, "action_taken": "; ".join(step.text for step in steps)}
    elif steps:
        update["steps"] = steps
    return report.model_copy(update=update)


def decision_in(decision: Decision, language: str) -> Decision:
    """The decision with its text in `language` when the text is Bleep's ("This is expected.")."""
    if decision.shown is None:
        return decision
    text = _rendered(decision.shown, language, decision.text or "")
    return decision.model_copy(update={"text": text})
