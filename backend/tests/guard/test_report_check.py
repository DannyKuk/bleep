from datetime import UTC, datetime

from bleep.guard.pipeline import guard_text
from bleep.guard.report_check import MARKER, WINDOW, check_report
from bleep.schemas.graph import Report

LINE = (
    "2026-11-02T12:00:01Z ERROR shop-web sqlalchemy.exc.OperationalError: could not translate "
    'host name "wrong-db" to address: Name or service not known (Background on this error at: '
    "https://sqlalche.me/e/20/e3q8)"
)
LOG = f"INFO starting shop-web\n{LINE}\nINFO shutting down\n"


def report(**fields) -> Report:
    base = {
        "title": "container died on shop",
        "summary": "shop-web stopped.",
        "cause": "Its database host is wrong.",
        "outcome": "Action taken, not verified",
    }
    return Report(**(base | fields))


class Records:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def __call__(self, kind: str, detail: str) -> None:
        self.records.append((kind, detail))


def test_a_pasted_log_line_is_replaced_and_the_rest_kept():
    assert len(LINE) >= 200
    records = Records()
    pasted = report(summary=f"shop-web crashed on start. The log said: {LINE}. It never came up.")

    checked = check_report(pasted, [LOG], records)

    assert LINE not in checked.summary
    assert "wrong-db" not in checked.summary
    said = f"shop-web crashed on start. The log said: {MARKER}. It never came up."
    assert checked.summary == said
    assert records.records == [("redacted", "1 log excerpt(s) removed from the report")]


def test_every_written_field_is_checked():
    checked = check_report(
        report(cause=LINE, action_taken=f"restarted; {LINE}", follow_ups=["ok", LINE]),
        [LOG],
    )
    for text in (checked.cause, checked.action_taken, *checked.follow_ups):
        assert LINE not in text
    assert checked.cause == MARKER
    assert checked.follow_ups == ["ok", MARKER]


def test_text_the_guard_wrapped_is_matched_too():
    guarded, _ = guard_text("logs.shop-web-1", LOG)

    checked = check_report(report(summary=LINE), [guarded])

    assert checked.summary == MARKER


def test_a_copy_with_other_whitespace_still_counts():
    respaced = "  ".join(LINE.split(" "))

    checked = check_report(report(summary=f"Saw:\n{respaced}"), [LOG])

    assert checked.summary == f"Saw:\n{MARKER}"


def test_shorter_overlaps_are_kept():
    phrase = LINE[30 : 30 + WINDOW - 1]  # one character short of the threshold
    records = Records()

    checked = check_report(report(summary=f"The error was {phrase} on start."), [LOG], records)

    assert checked.summary == f"The error was {phrase} on start."
    assert records.records == []


def test_exactly_the_threshold_is_removed():
    phrase = LINE[30 : 30 + WINDOW]

    checked = check_report(report(summary=f"The error was {phrase} on start."), [LOG])

    assert checked.summary == f"The error was {MARKER} on start."


def test_a_one_character_change_does_not_split_the_marker():
    edited = LINE[:100] + "X" + LINE[101:]

    checked = check_report(report(summary=edited), [LOG])

    assert checked.summary == MARKER


def test_no_logs_changes_nothing():
    original = report(summary=LINE)
    assert check_report(original, []) == original


def test_timeline_and_title_are_left_alone():
    """Both are Bleep's own text, built in code."""
    from bleep.schemas.graph import TimelineEntry

    original = report(
        title=LINE,
        timeline=[TimelineEntry(at=datetime(2026, 11, 2, tzinfo=UTC), text=LINE)],
    )
    checked = check_report(original, [LOG])
    assert checked.title == LINE and checked.timeline[0].text == LINE
