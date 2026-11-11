"""An incident's stored fixed texts in the person's language, and what a drifted one does (#427)."""

import logging
from datetime import UTC, datetime

import pytest

from bleep.graph.nodes.report import ENTRY_CHARS, clip
from bleep.graph.shown import english, shown_payload
from bleep.incidents.shown import decision_in, event_in, report_in
from bleep.schemas.graph import Decision, Report, TimelineEntry
from bleep.schemas.incidents import IncidentEvent
from bleep.schemas.shown import Shown

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LEFT_ALONE = Shown.of("notice/left_alone", name="shop")
RENAMED = Shown(name="notice/renamed_later")
NO_NAME = Shown.model_validate({"name": "notice/left_alone"})  # stored without its `name` value


def event(payload: dict) -> IncidentEvent:
    return IncidentEvent(incident_id="i1", seq=1, kind="notice", payload=payload, at=T0)


def report(**fields) -> Report:
    return Report(**{"title": "t", "summary": "s", "cause": "c", "outcome": "o", **fields})


def test_an_event_reads_in_the_picked_language_and_keeps_what_it_is_stored_as():
    stored = event(shown_payload(LEFT_ALONE))
    assert event_in(stored, "de").payload["text"].startswith("Geschlossen:")
    assert event_in(stored, "en").payload["text"] == "Closed: you told me to leave shop alone."
    assert stored.payload["text"] == "Closed: you told me to leave shop alone."


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("drifted", [RENAMED, NO_NAME], ids=["renamed", "missing-value"])
def test_an_event_whose_template_drifted_keeps_its_stored_english(drifted, language, caplog):
    stored = event({"text": "Closed: kept.", "shown": drifted.stored()})
    with caplog.at_level(logging.WARNING):
        assert event_in(stored, language) == stored
    assert "does not render" in caplog.text


@pytest.mark.parametrize("language", ["en", "de"])
def test_an_event_whose_shown_is_not_a_shown_text_is_left_as_it_is(language):
    stored = event({"text": "Closed: kept.", "shown": {"values": "not a mapping"}})
    assert event_in(stored, language) == stored


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("drifted", [RENAMED, NO_NAME], ids=["renamed", "missing-value"])
def test_a_report_with_a_drifted_text_keeps_that_text_and_renders_the_rest(drifted, language):
    entry = TimelineEntry(at=T0, text="Gathered the context", shown=Shown.of("timeline/gathered"))
    stale = TimelineEntry(at=T0, text="Stored line.", shown=drifted)
    stored = report(
        outcome="Fixed and verified",
        timeline=[stale, entry],
        shown={"outcome": drifted, "cause": Shown.of("report/not_determined")},
        human_decision="Approved on Telegram",
    )

    shown = report_in(stored, language)

    assert shown.outcome == "Fixed and verified"
    assert shown.timeline[0].text == "Stored line."
    assert shown.timeline[1].text == ("Kontext gesammelt" if language == "de" else entry.text)
    assert shown.cause == ("Nicht bestimmt." if language == "de" else "Not determined.")


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("drifted", [RENAMED, NO_NAME], ids=["renamed", "missing-value"])
def test_a_decision_with_a_drifted_text_keeps_its_stored_english(drifted, language):
    stored = Decision(kind="reject", text="This is expected.", via="telegram", at=T0, shown=drifted)
    assert decision_in(stored, language) == stored


def test_a_timeline_line_is_cut_to_the_length_it_has_in_english_after_it_is_rendered():
    steps = "; ".join(f"docker.restart shop-service-{n} on a-host" for n in range(12))
    proposed = Shown.of("timeline/proposed", steps=steps)
    entry = TimelineEntry(at=T0, text=clip(english(proposed), ENTRY_CHARS), shown=proposed)
    assert len(entry.text) == ENTRY_CHARS

    line = report_in(report(timeline=[entry]), "de").timeline[0].text

    assert line.startswith("Vorgeschlagen: docker.restart shop-service-0")
    assert len(line) == ENTRY_CHARS
    assert line.endswith("…")


def test_a_short_timeline_line_is_not_cut():
    gathered = Shown.of("timeline/gathered")
    entry = TimelineEntry(at=T0, text=english(gathered), shown=gathered)
    assert report_in(report(timeline=[entry]), "de").timeline[0].text == "Kontext gesammelt"
