"""Markdown into chunks small enough to embed, each with the path of headings above it.

A page is cut at its headings first, so a chunk never mixes two sections, and every chunk knows
where it sits ("Compose services > healthcheck"). A section longer than `max_tokens` is cut at
line boundaries, at the end of a paragraph when one falls in the second half of the chunk, and
the next chunk starts with up to `overlap` tokens of the lines before the cut. Tokens are
estimated (a word or a punctuation mark each), which runs below the embedding model's own count;
see docs/design/knowledge.md for the limit this keeps under.
"""

import re
from dataclasses import dataclass

MAX_TOKENS = 300
OVERLAP = 50

# Letters run as one token; digits in threes and every mark (underscore too) as one each, because
# the embedding model splits numbers and snake_case names that finely.
_TOKEN = re.compile(r"[^\W\d_]+|\d{1,3}|[^\w\s]|_")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_ANCHOR = re.compile(r"\s*\{#[^}]*\}$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")
_SPACES = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True)
class Chunk:
    heading_path: tuple[str, ...]
    text: str


def estimate_tokens(text: str) -> int:
    """About how many tokens `text` is, as the embedding model counts them."""
    return len(_TOKEN.findall(text))


def _heading_title(raw: str) -> str:
    return _ANCHOR.sub("", raw).replace("`", "").strip()


def _tidy(line: str) -> str:
    """A table row without its column padding; other lines as they are."""
    if not line.startswith("|"):
        return line
    if _TABLE_RULE.match(line):
        cells = line.strip("|").split("|")
        return "| " + " | ".join("---" for _ in cells) + " |"
    return _SPACES.sub(" ", line.rstrip())


def _sections(text: str) -> list[tuple[tuple[str, ...], list[str]]]:
    """The text's sections in order: the heading path and the lines under it."""
    sections: list[tuple[tuple[str, ...], list[str]]] = [((), [])]
    stack: list[tuple[int, str]] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            stack = [entry for entry in stack if entry[0] < level]
            stack.append((level, _heading_title(heading.group(2))))
            sections.append((tuple(title for _, title in stack), []))
        else:
            sections[-1][1].append(line if in_fence else _tidy(line))
    return sections


def _split_line(line: str, limit: int) -> list[str]:
    """A line over `limit` tokens as pieces of whole words, each within it."""
    if estimate_tokens(line) <= limit:
        return [line]
    pieces, current = [], []
    for word in line.split():
        if current and estimate_tokens(" ".join([*current, word])) > limit:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _tail(lines: list[str], budget: int) -> list[str]:
    """The last lines of `lines` that fit in `budget` tokens, without leading blank lines."""
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = estimate_tokens(line)
        if used + cost > budget:
            break
        kept.insert(0, line)
        used += cost
    while kept and not kept[0].strip():
        kept.pop(0)
    return kept


def _cost(lines: list[str]) -> int:
    return sum(estimate_tokens(line) for line in lines)


def _paragraph_break(lines: list[str], limit: int) -> int | None:
    """The index of the last blank line with at least half the limit's tokens before it."""
    used = 0
    best = None
    for index, line in enumerate(lines):
        if not line.strip() and used * 2 >= limit:
            best = index
        used += estimate_tokens(line)
    return best


def _pack(lines: list[str], max_tokens: int, overlap: int) -> list[str]:
    units = [piece for line in lines for piece in _split_line(line, max_tokens)]
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for unit in units:
        cost = estimate_tokens(unit)
        if used + cost > max_tokens and used > 0:
            cut = _paragraph_break(current, max_tokens)
            # Cutting at the paragraph is only worth it when what follows still fits beside unit.
            if cut is not None and _cost(current[cut + 1 :]) + cost > max_tokens:
                cut = None
            done, rest = (current, []) if cut is None else (current[:cut], current[cut + 1 :])
            chunks.append("\n".join(done))
            carried = _tail(done, min(overlap, max_tokens - cost - _cost(rest)))
            current = [*carried, *([""] if carried and rest else []), *rest]
            used = _cost(current)
        current.append(unit)
        used += cost
    chunks.append("\n".join(current))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def chunk_markdown(text: str, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP) -> list[Chunk]:
    """The markdown `text` as chunks of at most `max_tokens` estimated tokens, in order."""
    return [
        Chunk(path, piece)
        for path, lines in _sections(text)
        for piece in _pack(lines, max_tokens, overlap)
    ]
