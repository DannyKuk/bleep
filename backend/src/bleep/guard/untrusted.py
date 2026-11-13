"""Label text from outside Bleep as data, so the model never reads it as an instruction."""

import functools
import re
import secrets
import unicodedata

SOURCE = re.compile(r"[a-z0-9_.:-]{1,40}")
NONCE = re.compile(r"[a-z0-9]{4,32}")

_LT_LOOKALIKES = "\u02c2\u1438\u2770\u276e\u276c\u2329\u27e8\u27ea\u2039\u00ab\u300a\u3008"
_LT_LOOKALIKES += "\u227a\u22d6\u25c1\u25c0\u29c0\u2a7b\u2a99"


def _candidate_class() -> re.Pattern[str]:
    """`<` and every BMP character that folds to it (＜ ﹤ ≮ …), plus the look-alikes above."""
    chars = {"<", *_LT_LOOKALIKES}
    chars.update(
        chr(cp) for cp in range(128, 0x10000) if unicodedata.normalize("NFKD", chr(cp))[:1] == "<"
    )
    return re.compile("[" + "".join(re.escape(c) for c in sorted(chars)) + "]")


_CANDIDATE = _candidate_class()
# `&#60;/untrusted` and `&#x3c;/untrusted` read as a tag once decoded
_NUMERIC_ENTITY = re.compile(r"&(?=#(?:0*60|x0*3c);\s*/*\s*untrusted)", re.IGNORECASE)
# Letters of "untrusted" that have look-alikes in other scripts (Cyrillic, Greek, Armenian, small
# capitals); NFKC already folds full-width and mathematical letters.
_CONFUSABLE = str.maketrans(
    {
        "\u0443": "u", "\u03c5": "u", "\u057d": "u", "\u0446": "u", "\u1d1c": "u",
        "\u0578": "n", "\u043f": "n", "\u03b7": "n", "\u0274": "n",
        "\u0442": "t", "\u03c4": "t", "\u1d1b": "t",
        "\u0433": "r", "\u0280": "r",
        "\u0455": "s", "\ua731": "s",
        "\u0435": "e", "\u03b5": "e", "\u1d07": "e",
        "\u0501": "d", "\u1d05": "d",
        "\u0430": "a", "\u043e": "o", "\u03bf": "o", "\u0440": "p", "\u0441": "c",
        "\u0456": "i", "\u2215": "/", "\u2044": "/",
    }
)  # fmt: skip
_FOLD_WINDOW = 256
_TAG = re.compile(r"[\s/\\]*untrusted")
_ESCAPED_LT = re.compile("&lt;", re.IGNORECASE)
_LEAD = re.compile(r"[\s/\\]")
_WORD = "untrusted"
# Bleep's own wrapper (`wrap_untrusted`) as a record that copied it from a prompt stores it,
# escaped: an opening tag and the closing tag with the same id.
_COPIED_WRAPPER = re.compile(
    rf'&lt;untrusted source="{SOURCE.pattern}" id="({NONCE.pattern})">(.*?)&lt;/untrusted id="\1">',
    re.DOTALL,
)
_OPENING = re.compile(rf'<untrusted source="{SOURCE.pattern}" id="({NONCE.pattern})">\n')


def fold(chunk: str) -> str:
    """Fold a short stretch of text the way a reader would see it: NFKC, no invisible, control
    or combining characters, look-alike letters mapped to Latin, lower case."""
    folded = unicodedata.normalize("NFKC", chunk)
    folded = "".join(c for c in folded if unicodedata.category(c) not in ("Cf", "Cc", "Mn", "Me"))
    return folded.lower().translate(_CONFUSABLE)


def _neutralise(match: re.Match[str]) -> str:
    window = match.string[match.end() : match.end() + _FOLD_WINDOW]
    return "&lt;" if _TAG.match(fold(window)) else match.group(0)


@functools.lru_cache(maxsize=4096)
def _fold_char(char: str) -> str:
    return fold(char)


def _fold_sequence(sequence: str) -> str:
    return _fold_char(sequence) if len(sequence) == 1 else fold(sequence)


def _starts_tag(text: str, start: int) -> bool:
    """What `_TAG.match(fold(window))` says for the window at `start`, folded one combining
    sequence at a time and stopped at the first folded character that cannot lead to the tag, so
    the work stays linear in the text however many `&lt;` it holds. A sequence (a character and
    the combining marks after it) is what NFKC composes, so folding them one by one gives the
    same characters as folding the window."""
    end = min(len(text), start + _FOLD_WINDOW)
    matched, i = 0, start
    while i < end:
        j = i + 1
        while j < end and unicodedata.combining(text[j]):
            j += 1
        for c in _fold_sequence(text[i:j]):
            if c == _WORD[matched]:
                matched += 1
                if matched == len(_WORD):
                    return True
            elif matched or not _LEAD.match(c):
                return False
        i = j
    return False


def holds_escaped_tag(text: str) -> bool:
    """Whether text holds a wrapper tag `escape_untrusted` escaped (`&lt;`, in any case, before a
    tag folded as the escape folds it): text that tried to end the data early, or stored text that
    copied a wrapper from a prompt."""
    return any(_starts_tag(text, m.end()) for m in _ESCAPED_LT.finditer(text))


def without_copied_wrappers(text: str) -> str:
    """Bleep's record with the escaped copies of Bleep's own wrapper taken out, their text kept:
    an opening tag and the closing tag with the same id, as a cause that copied a wrapped name
    from its prompt holds them (#310)."""
    return _COPIED_WRAPPER.sub(lambda m: f" {m[2]} ", text)


def escape_untrusted(text: str) -> str:
    """Turn every `<` that starts an `untrusted` tag into `&lt;`.

    The check runs on folded text, so full-width, small-form and mathematical brackets, Cyrillic
    or Greek letters, soft hyphens, combining marks and long runs of spaces or slashes do not hide
    a tag. Other angle brackets are left alone so logs stay readable.
    """
    text = _NUMERIC_ENTITY.sub("&amp;", text)
    return _CANDIDATE.sub(_neutralise, text)


def is_wrapped(text: str) -> bool:
    """Whether `text` is exactly one wrapper as `wrap_untrusted` writes it, and nothing else: its
    opening tag, text with no tag of its own (`wrap_untrusted` escapes every one) and the closing
    tag with the same id. Read without a regex over the text, so a long one costs one scan."""
    opening = _OPENING.match(text)
    if opening is None:
        return False
    closing = f'\n</untrusted id="{opening[1]}">'
    if not text.endswith(closing) or len(text) < opening.end() + len(closing):
        return False
    body = text[opening.end() : len(text) - len(closing)]
    return "<untrusted" not in body and "</untrusted" not in body


def wrap_untrusted(source: str, text: str, nonce: str | None = None) -> str:
    """Return `text` inside `<untrusted source="…" id="…">…</untrusted id="…">`.

    `id` is random per call, so only the closing tag carrying that same id ends the data; a
    forged `</untrusted>` cannot match it, and tag look-alikes in the text are escaped as well.
    `source` must match `[a-z0-9_.:-]{1,40}` so it cannot break out of the attribute.
    """
    if not SOURCE.fullmatch(source):
        raise ValueError(f"invalid untrusted source label: {source!r}")
    nonce = nonce or secrets.token_hex(6)
    if not NONCE.fullmatch(nonce):
        raise ValueError(f"invalid untrusted nonce: {nonce!r}")
    return (
        f'<untrusted source="{source}" id="{nonce}">\n{escape_untrusted(text)}\n'
        f'</untrusted id="{nonce}">'
    )
