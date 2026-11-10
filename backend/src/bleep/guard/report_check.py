"""Keep log text out of a written report: what the model copied from a log is cut out.

The model writes a report's summary, cause and follow-ups after reading logs, and a weak model
pastes them. `check_report` compares every written field with the log text the run collected (what
Gather and the tools handed the model) and replaces each stretch of at least `WINDOW` characters
that also appears in a log with `MARKER`. Whitespace is collapsed on both sides first, so a copy
that only re-spaced a line still counts. Title and timeline are Bleep's own text and are not
checked.
"""

from collections.abc import Callable, Iterable

from bleep.guard.log import GuardKind
from bleep.schemas.graph import Report

WINDOW = 60  # characters shared with a log that make a copy
MARKER = "[log excerpt removed]"
JOIN_GAP = 8  # copies this close together are cut as one, so an edited character does not split it

Record = Callable[[GuardKind, str], None]


def _collapse(text: str) -> tuple[str, list[int]]:
    """`text` with every run of whitespace as one space, and each kept character's position in
    `text`."""
    chars: list[str] = []
    where: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            if chars and chars[-1] == " ":
                continue
            char = " "
        chars.append(char)
        where.append(index)
    return "".join(chars), where


def _windows(logs: Iterable[str]) -> set[int]:
    """The hash of every `WINDOW`-character stretch of the logs, whitespace collapsed."""
    seen: set[int] = set()
    for log in logs:
        text, _ = _collapse(log)
        seen.update(hash(text[i : i + WINDOW]) for i in range(len(text) - WINDOW + 1))
    return seen


def _cut(text: str, windows: set[int]) -> tuple[str, int]:
    """`text` with each copied stretch replaced by `MARKER`, and how many were replaced."""
    flat, where = _collapse(text)
    spans: list[list[int]] = []
    for start in range(len(flat) - WINDOW + 1):
        if hash(flat[start : start + WINDOW]) not in windows:
            continue
        end = start + WINDOW
        if spans and start <= spans[-1][1] + JOIN_GAP:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    if not spans:
        return text, 0
    out, last = [], 0
    for start, end in spans:
        first, after = where[start], where[end - 1] + 1
        while text[first].isspace():  # the spaces around a copy are the report's own
            first += 1
        while text[after - 1].isspace():
            after -= 1
        out.append(text[last:first])
        out.append(MARKER)
        last = after
    out.append(text[last:])
    return "".join(out), len(spans)


def check_report(report: Report, raw_logs: Iterable[str], record: Record | None = None) -> Report:
    """The report with every stretch copied from `raw_logs` replaced by `MARKER`.

    Checks `summary`, `cause`, `cause_en`, `action_taken` and each follow-up. A replacement is
    recorded as `redacted` in the guard log, once per report.
    """
    windows = _windows(raw_logs)
    if not windows:
        return report
    removed = 0

    def cut(text: str) -> str:
        nonlocal removed
        checked, count = _cut(text, windows)
        removed += count
        return checked

    checked = report.model_copy(
        update={
            "summary": cut(report.summary),
            "cause": cut(report.cause),
            "cause_en": None if report.cause_en is None else cut(report.cause_en),
            "action_taken": None if report.action_taken is None else cut(report.action_taken),
            "follow_ups": [cut(item) for item in report.follow_ups],
        }
    )
    if removed and record is not None:
        record("redacted", f"{removed} log excerpt(s) removed from the report")
    return checked if removed else report
