"""Label text from outside Bleep as data, so the model never reads it as an instruction."""

import re
import secrets
import unicodedata

SOURCE = re.compile(r"[a-z0-9_.:-]{1,40}")
NONCE = re.compile(r"[a-z0-9]{4,32}")

# Anything that folds to `<` (＜ ﹤ ‹ ⟨ 〈 ≮ …) is a candidate tag start, as is `<` itself.
_LT_LOOKALIKES = set("＜﹤‹⟨〈❮〈«⟪《❬")
# Letters of "untrusted" that have look-alikes in other scripts (Cyrillic, Greek, Armenian).
_CONFUSABLE = str.maketrans(
    {
        "у": "u",  # Cyrillic у reads as u in many fonts
        "υ": "u",
        "ս": "u",
        "ц": "u",
        "ո": "n",
        "п": "n",
        "т": "t",
        "τ": "t",
        "г": "r",
        "ѕ": "s",
        "е": "e",
        "ε": "e",
        "ԁ": "d",
        "а": "a",
        "о": "o",
        "ο": "o",
        "р": "p",
        "с": "c",
        "і": "i",
        "∕": "/",
        "⁄": "/",
    }
)
_FOLD_WINDOW = 48
_TAG = re.compile(r"[\s/\\]*untrusted")


def _fold(chunk: str) -> str:
    """Fold a short stretch of text the way a reader would see it: NFKC, no invisible or
    combining characters, look-alike letters mapped to Latin, lower case."""
    folded = unicodedata.normalize("NFKC", chunk)
    folded = "".join(c for c in folded if unicodedata.category(c) not in ("Cf", "Cc", "Mn", "Me"))
    return folded.lower().translate(_CONFUSABLE)


def _starts_tag(text: str, index: int) -> bool:
    return _TAG.match(_fold(text[index + 1 : index + 1 + _FOLD_WINDOW])) is not None


def _is_lt(char: str) -> bool:
    return char == "<" or char in _LT_LOOKALIKES or unicodedata.normalize("NFKD", char)[:1] == "<"


def escape_untrusted(text: str) -> str:
    """Turn every `<` that starts an `untrusted` tag into `&lt;`.

    The check is made on folded text, so full-width, small-form and mathematical brackets,
    Cyrillic or Greek letters, soft hyphens and combining marks do not hide a tag. Other angle
    brackets are left alone so logs stay readable.
    """
    if not any(c == "<" or ord(c) > 127 for c in text):
        return text
    out: list[str] = []
    for index, char in enumerate(text):
        if _is_lt(char) and _starts_tag(text, index):
            out.append("&lt;")
        else:
            out.append(char)
    return "".join(out)


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
