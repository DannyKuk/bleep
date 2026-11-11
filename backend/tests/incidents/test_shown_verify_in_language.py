"""Verify's checks and the report's steps and planted-orders note in the person's language, and
what a drifted one does (#454)."""

import logging
from datetime import UTC, datetime

import pytest

from bleep.graph.planted import planted_sentence
from bleep.graph.shown import english
from bleep.guard.report_check import MARKER, check_report
from bleep.incidents.shown import check_in, event_in, report_in, verification_in
from bleep.schemas.graph import Check, Report, ReportStep, VerificationResult
from bleep.schemas.incidents import IncidentEvent
from bleep.schemas.shown import Shown

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EXITED = Check(
    kind="container",
    target="shop-web-1",
    ok=False,
    detail="is exited",
    shown=Shown.of("verify/is_exited"),
)
STOPPED = Check(
    kind="container",
    target="shop-db-1 (stopped by the plan)",
    ok=True,
    detail="is exited",
    target_shown=Shown.of("verify/target_stopped", name="shop-db-1"),
    shown=Shown.of("verify/is_exited"),
)
OLD = Check(kind="url", target="http://shop", ok=False, detail="failed")  # stored before #454
RENAMED = Shown(name="verify/renamed_later")


def verification(*checks) -> IncidentEvent:
    payload = {"ok": False, "attempt": 1, "checks": list(checks)}
    return IncidentEvent(incident_id="i1", seq=1, kind="verification", payload=payload, at=T0)


def report(**fields) -> Report:
    return Report(**{"title": "t", "summary": "s", "cause": "c", "outcome": "o", **fields})


def test_a_verification_event_reads_in_the_picked_language_and_keeps_what_it_is_stored_as():
    stored = verification(EXITED.stored(), STOPPED.stored(), OLD.stored())

    german = event_in(stored, "de").payload["checks"]
    in_english = event_in(stored, "en").payload["checks"]

    assert [(c["target"], c["detail"]) for c in german] == [
        ("shop-web-1", "ist beendet"),
        ("shop-db-1 (vom Plan gestoppt)", "ist beendet"),
        ("http://shop", "failed"),  # stored before #454, as it is
    ]
    assert [(c["target"], c["detail"]) for c in in_english] == [
        ("shop-web-1", "is exited"),
        ("shop-db-1 (stopped by the plan)", "is exited"),
        ("http://shop", "failed"),
    ]
    assert stored.payload["checks"][0]["detail"] == "is exited"
    assert german[0]["ok"] is False and german[0]["shown"] == {"name": "verify/is_exited"}


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_check_whose_template_drifted_keeps_its_stored_english(language, caplog):
    drifted = EXITED.stored() | {"shown": RENAMED.stored()}
    stored = verification(drifted)
    with caplog.at_level(logging.WARNING):
        shown = event_in(stored, language)
    assert shown.payload["checks"][0]["detail"] == "is exited"
    assert "does not render" in caplog.text


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize(
    "payload",
    [{"ok": True}, {"checks": []}, {"checks": ["not a check", {"kind": "nope"}]}],
    ids=["no-checks", "empty", "malformed"],
)
def test_a_verification_event_that_is_no_list_of_checks_is_left_as_it_is(payload, language):
    stored = IncidentEvent(incident_id="i1", seq=1, kind="verification", payload=payload, at=T0)
    assert event_in(stored, language) == stored


def test_a_check_and_a_result_in_a_language_are_plain_text():
    shown = check_in(STOPPED, "de")
    assert (shown.target, shown.detail) == ("shop-db-1 (vom Plan gestoppt)", "ist beendet")
    assert shown.shown is None and shown.target_shown is None
    result = verification_in(VerificationResult(ok=False, checks=[EXITED]), "de")
    assert result.checks[0].detail == "ist beendet"
    assert english(result.lines()).startswith("FAILED container shop-web-1: ist beendet")


STEP = Shown.of(
    "step/line", action="docker.start", target="web", host="a", status=Shown.of("step/done")
)


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_report_with_a_drifted_step_keeps_that_step_and_renders_the_rest(language):
    ok = ReportStep(text=english(STEP), status="done", shown=STEP)
    stale = ReportStep(text="docker.stop web on a: failed", status="failed", shown=RENAMED)
    stored = report(action_taken=f"{ok.text}; {stale.text}", steps=[ok, stale])

    shown = report_in(stored, language)

    german = language == "de"
    assert shown.steps[0].text == ("docker.start web auf a: erledigt" if german else ok.text)
    assert shown.steps[1].text == stale.text
    assert [s.status for s in shown.steps] == ["done", "failed"]
    assert shown.action_taken == "; ".join(s.text for s in shown.steps)


def test_an_action_text_that_is_not_the_steps_joined_is_shown_as_stored():
    step = ReportStep(text=english(STEP), status="done", shown=STEP)
    stored = report(action_taken=f"{MARKER} on a: done", steps=[step])

    shown = report_in(stored, "de")

    assert shown.action_taken == stored.action_taken
    assert shown.steps[0].text == "docker.start web auf a: erledigt"


def test_a_report_stored_before_the_steps_field_shows_its_action_as_stored():
    stored = report(action_taken="docker.start web on a: done")
    assert report_in(stored, "de").action_taken == "docker.start web on a: done"
    assert report_in(stored, "de").steps == []


def test_a_planted_note_is_shown_in_its_place_and_one_out_of_range_is_ignored():
    note = Shown.of(
        "planted/sentence", where=Shown.of("planted/where_logs"), end=Shown.of("planted/end")
    )
    stored = report(
        follow_ups=[planted_sentence(["logs"]) or "", "Set a restart policy."],
        shown_follow_ups={0: note, 7: note},
    )

    german = report_in(stored, "de").follow_ups

    assert german[0].startswith("Bleep hat in den Container-Logs Text gefunden")
    assert german[1] == "Set a restart policy."
    assert report_in(stored, "en").follow_ups == stored.follow_ups


def test_a_log_copy_in_a_step_is_cut_and_the_cut_step_shows_as_stored():
    line = (
        "2026-11-02T12:00:01Z ERROR shop-web sqlalchemy.exc.OperationalError: could not translate"
    )
    copied = ReportStep(text=f"docker.start {line} on a: done", status="done", shown=STEP)
    clean = ReportStep(text=english(STEP), status="done", shown=STEP)
    stored = report(action_taken=f"{copied.text}; {clean.text}", steps=[copied, clean])

    checked = check_report(stored, [line + " and more of the same line, long enough to be a copy"])

    assert MARKER in checked.steps[0].text and checked.steps[0].shown is None
    assert checked.steps[1] == clean


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_log_copy_spanning_two_steps_stays_cut_in_every_view(language):
    first = Shown.of(
        "step/line",
        action="docker.start",
        target="shop-web-1",
        host="a",
        status=Shown.of("step/done"),
    )
    second = Shown.of(
        "step/line",
        action="docker.stop",
        target="shop-db-1",
        host="a",
        status=Shown.of("step/done"),
    )
    steps = [ReportStep(text=english(s), status="done", shown=s) for s in (first, second)]
    joined = "; ".join(s.text for s in steps)
    assert len(joined) >= 60 and all(len(s.text) < 60 for s in steps)
    stored = report(action_taken=joined, steps=steps)

    checked = check_report(stored, [joined + " and the rest of the logged line goes on and on"])
    shown = report_in(checked, language)

    assert MARKER in checked.action_taken
    assert shown.action_taken == checked.action_taken
    assert shown.steps == []  # the joined text is all there is to show
    assert "docker.start" not in shown.action_taken
