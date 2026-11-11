"""Keep a commit sha in the report only when the run read that commit from GitHub (#350).

A report that names the commit behind an incident ("commit 3f204d3 'Deploy shop 2' changed …")
lets the human open, revert or blame it. A sha the model made up sends them to the wrong commit
or to none, so code checks every sha the report writes against the commits the run read: those
`recent_commits` lists and the one `commit_diff` shows, read from where the tools write a sha
(`tools/github.describe_commits`, `describe_diff`) inside Investigate's wrapper for that tool. A
sha in a log, in another tool's output, in a commit message or a patch, or in a GitHub failure
sentence was not read as a commit and does not count.
"""

import re
from collections.abc import Iterable

from langchain_core.messages import AnyMessage, ToolMessage

from bleep.guard.report_check import Record
from bleep.schemas.graph import Report

# Investigate's wrapper, named after the tool, then the tool's own first line: the list's head,
# or the diff's "commit <sha> by …", which carries the sha GitHub answered with.
_COMMITS_HEAD = re.compile(r'<untrusted source="recent_commits" id="[a-z0-9]{4,32}">\nCommits to ')
_DIFF_HEAD = re.compile(
    r'<untrusted source="commit_diff" id="[a-z0-9]{4,32}">\ncommit ([0-9a-f]{7,40}) by '
)
# One commit of the list: "- <sha> <date> UTC <author>: <message>". A message's own lines are
# indented, though a carriage return in one (a line break once guarded) can still start a line
# this way: such a sha came from the repo's text, which is what counts, not from the model.
_LISTED = re.compile(r"^- ([0-9a-f]{7,40}) \d{4}-\d\d-\d\d \d\d:\d\d UTC ", re.MULTILINE)

# What reads as a sha in a report: a word of 7 hex characters mixing digits and letters, as the
# tools show a sha (so neither "defaced" nor "1048576" is one), or any word of 7 to 40 hex
# characters right after "commit". A word that is part of a name, a path, an image tag or a
# value (`shop:3f204d3`, `REV=3f204d3`) is not one.
_EDGE_BEFORE = r"(?<![\w./:@#=-])"
_EDGE_AFTER = r"(?![\w/:@-])(?!\.\w)"
_SHORT_SHA = re.compile(
    rf"{_EDGE_BEFORE}(?=[0-9a-f]{{0,6}}[0-9])(?=[0-9a-f]{{0,6}}[a-f])[0-9a-f]{{7}}{_EDGE_AFTER}",
    re.IGNORECASE,
)
_AFTER_COMMIT = re.compile(
    rf"\bcommits?[\s`'\"(:]{{1,3}}{_EDGE_BEFORE}([0-9a-f]{{7,40}}){_EDGE_AFTER}", re.IGNORECASE
)
_EMPTY_QUOTE = re.compile(r"\(\s*\)|\[\s*\]|`\s*`|'\s*'|\"\s*\"")
_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_MARK = re.compile(r"[ \t]+(?=[,.;:!?)])")


def read_shas(messages: Iterable[AnyMessage]) -> set[str]:
    """The shas of the commits the run's GitHub tools listed or showed, as they wrote them."""
    read: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        text = message.text
        if message.name == "recent_commits" and _COMMITS_HEAD.match(text):
            read.update(_LISTED.findall(text))
        elif message.name == "commit_diff" and (head := _DIFF_HEAD.match(text)):
            read.add(head[1])
    return read


def _spans(text: str) -> list[tuple[int, int]]:
    """Where `text` writes a sha, in order, each once."""
    found = {m.span() for m in _SHORT_SHA.finditer(text)}
    found |= {m.span(1) for m in _AFTER_COMMIT.finditer(text)}
    return sorted(found)


def _kept(written: str, read: set[str]) -> str:
    """`written` when it is a read sha or the start of one; cut to the read sha it starts with,
    which drops what the run never saw; else nothing."""
    sha = written.lower()
    if any(known.startswith(sha) for known in read):
        return written
    longest = max((known for known in read if sha.startswith(known)), key=len, default="")
    return written[: len(longest)]


def _tidy(text: str) -> str:
    """`text` without the brackets, quotes and spaces a removed sha left empty."""
    text = _EMPTY_QUOTE.sub("", text)
    return _SPACE_BEFORE_MARK.sub("", _SPACES.sub(" ", text)).strip()


def _checked(text: str, read: set[str]) -> tuple[str, int]:
    """`text` with each sha the run did not read removed, and how many were."""
    spans = _spans(text)
    if not spans:
        return text, 0
    out, last, dropped = [], 0, 0
    for start, end in spans:
        kept = _kept(text[start:end], read)
        dropped += not kept
        out += [text[last:start], kept]
        last = end
    joined = "".join([*out, text[last:]])
    return (_tidy(joined) if dropped else joined), dropped


def check_shas(report: Report, read: set[str], record: Record | None = None) -> Report:
    """The report with every commit sha the run did not read (`read_shas`) removed from the
    model's text: the summary, the cause, its English copy and the follow-ups. A longer sha that
    starts with a read one is cut to it. A removal is recorded as `redacted`, once per report."""
    dropped = 0

    def check(text: str) -> str:
        nonlocal dropped
        checked, count = _checked(text, read)
        dropped += count
        return checked

    update = {
        "summary": check(report.summary),
        "cause": check(report.cause),
        "cause_en": None if report.cause_en is None else check(report.cause_en),
        "follow_ups": [item for item in map(check, report.follow_ups) if item],
    }
    if dropped and record is not None:
        record("redacted", f"{dropped} commit sha(s) the run never read removed from the report")
    changed = any(getattr(report, key) != value for key, value in update.items())
    return report.model_copy(update=update) if changed else report
