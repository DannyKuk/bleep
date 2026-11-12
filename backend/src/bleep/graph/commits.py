"""Keep a commit sha in what the model writes only when the run read that commit from GitHub
(#350).

A diagnosis or a report that names the commit behind an incident ("commit 3f204d3 'Deploy shop
2' changed …") lets the human open, revert or blame it. A sha the model made up sends them to the
wrong commit or to none, so code checks every sha the model writes against the commits the run
read: those `recent_commits` lists and the one `commit_diff` shows, read from where the tools
write a sha (`tools/github.describe_commits`, `describe_diff`) inside Investigate's wrapper for
that tool. The tools end a commit message's first line at any line break and print an author, a
path and the repo on one line, so none of them can start a list line or a diff head. A sha in a
log, in another tool's output, in a commit message or a patch, or in a GitHub failure sentence
was not read as a commit and does not count.
"""

import re
from collections.abc import Iterable

from langchain_core.messages import AnyMessage, ToolMessage

from bleep.guard.report_check import Record

# Investigate's wrapper, named after the tool, then the tool's own first line: the list's head,
# or the diff's "commit <sha> by …", which carries the sha GitHub answered with. Both tools write
# the first 7 characters of a sha.
_COMMITS_HEAD = re.compile(r'<untrusted source="recent_commits" id="[a-z0-9]{4,32}">\nCommits to ')
_DIFF_HEAD = re.compile(
    r'<untrusted source="commit_diff" id="[a-z0-9]{4,32}">\ncommit ([0-9a-f]{7}) by '
)
_LISTED = re.compile(r"^- ([0-9a-f]{7}) \d{4}-\d\d-\d\d \d\d:\d\d UTC ", re.MULTILINE)

# A word of 7 to 40 hex characters as a text writes it, not inside a name, a path, an image tag, a
# value or a URL (`shop-3f204d3`, `shop:3f204d3`, `REV=3f204d3`, `…/commit/3f204d3`); a period or
# a colon after it ends it. The eval measure reads shas with it too (`evals.github`).
HEX_WORD = re.compile(r"(?<![\w./:@#=-])[0-9a-f]{7,40}(?![\w/@-])(?![.:]\w)", re.IGNORECASE)
# The brackets and quotes a text puts around a sha, each with the marks that close it: German
# writes „…“ and ‚…‘ (or „…”), French «…» and ‹…›, German also »…« and ›…‹.
_PAIRS = {
    "(": ")",
    "[": "]",
    "`": "`",
    "'": "'",
    '"': '"',
    "„": "“”",
    "‚": "‘’",
    "“": "”",
    "‘": "’",
    "«": "»",
    "»": "«",
    "‹": "›",
    "›": "‹",
}
# Words that say the hex word after them is a commit: "commit 1234567", "Commit-Hash 9c0ffee1",
# "commit ID: 9c0ffee1", "sha 1234567", "Revert 3f204d3", "Commit „9c0ffee1“"; a space, a colon
# or an opening mark may stand between them.
_CUE = re.compile(
    r"\b(?:commits?|sha|hash|revert(?:s|ed)?)(?:[-\s](?:hash|sha|id))?"
    rf"[\s:{re.escape(''.join(_PAIRS))}]{{1,3}}\Z",
    re.IGNORECASE,
)
CUE_ROOM = 24  # characters before a hex word where its cue is looked for
SHORT_SHA = 7  # the GitHub tools write the first 7 characters of a sha
_ENDS_CLAUSE = " ,.;:!?)"
# Words that say nothing once the sha after them is gone: the cues and the articles.
_EMPTY_WORDS = {"commit", "commits", "sha", "hash", "id", "revert", "reverts", "reverted"}
_EMPTY_WORDS |= {"a", "an", "the", "der", "die", "das", "ein", "eine", "einen"}
_WORD = re.compile(r"[^\W\d_]+")


def read_shas(messages: Iterable[AnyMessage]) -> set[str]:
    """The short shas of the commits the run's GitHub tools listed or showed."""
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


def written_shas(text: str, read: set[str]) -> list[tuple[int, int]]:
    """Where `text` writes a commit sha: a hex word after a cue ("commit", "sha", …), whatever
    its length and letters, or a word with no cue that reads as one (`_bare`)."""
    return [
        m.span()
        for m in HEX_WORD.finditer(text)
        if _cued(text, m.start()) or _bare(m[0].lower(), read)
    ]


def _cued(text: str, start: int) -> bool:
    return _CUE.search(text[max(0, start - CUE_ROOM) : start]) is not None


def _bare(word: str, read: set[str]) -> bool:
    """Whether a hex word with no cue reads as a sha: one that starts a sha the run read or starts
    with one, or a word as short as the tools write a sha that mixes digits and letters, as a sha
    almost always does, so neither "defaced" nor "1048576" is one. A longer word with no cue is a
    trace id, a checksum, a uuid or Docker's id of a container, which the text may need."""
    if any(known.startswith(word) or word.startswith(known) for known in read):
        return True
    mixed = any(c.isdigit() for c in word) and any(not c.isdigit() for c in word)
    return mixed and len(word) == SHORT_SHA


def _kept(written: str, read: set[str]) -> str:
    """`written` when it is a read sha or the start of one; cut to the read sha it starts with,
    which drops what the run never saw; else nothing."""
    sha = written.lower()
    if any(known.startswith(sha) for known in read):
        return written
    longest = max((known for known in read if sha.startswith(known)), key=len, default="")
    return written[: len(longest)]


def _removed(text: str, start: int, end: int) -> tuple[int, int]:
    """The stretch to remove for the sha at `start:end`: with the brackets or quotes around it
    that it alone fills, and the space before it when the clause goes on or ends right after."""
    if start > 0 and end < len(text) and text[end] in _PAIRS.get(text[start - 1], ""):
        start, end = start - 1, end + 1
    if start > 0 and text[start - 1] == " " and (end == len(text) or text[end] in _ENDS_CLAUSE):
        start -= 1
    elif start == 0 and text[end : end + 1] == " ":
        end += 1
    return start, end


def _nearly_empty(text: str) -> bool:
    """Whether nothing is left to read but cue words and articles ("The commit.")."""
    return not any(w.lower() not in _EMPTY_WORDS for w in _WORD.findall(text))


def drop_unread(text: str, read: set[str]) -> tuple[str, int]:
    """`text` with each sha the run did not read removed, and how many were. A longer sha that
    starts with a read one is cut to it. A text that a removal leaves nearly empty is empty, so
    the caller's fallback takes its place."""
    out, last, dropped = [], 0, 0
    for start, end in written_shas(text, read):
        kept = _kept(text[start:end], read)
        if kept:
            out += [text[last:start], kept]
            last = end
            continue
        start, end = _removed(text, max(start, last), end)
        out.append(text[last:start])
        last, dropped = end, dropped + 1
    if not out:
        return text, 0
    checked = "".join([*out, text[last:]])
    return ("" if dropped and _nearly_empty(checked) else checked), dropped


class ShaCheck:
    """Checks each text the model wrote for one record (a diagnosis, a report) against the shas
    the run read, counting what it removes, so the guard log gets one line per record."""

    def __init__(self, read: set[str]) -> None:
        self.read = read
        self.dropped = 0

    def __call__(self, text: str) -> str:
        checked, dropped = drop_unread(text, self.read)
        self.dropped += dropped
        return checked

    def record_to(self, record: Record, where: str) -> None:
        """Record what was removed from `where` as `redacted`, if anything was."""
        if self.dropped:
            record(
                "redacted", f"{self.dropped} commit sha(s) the run never read removed from {where}"
            )
