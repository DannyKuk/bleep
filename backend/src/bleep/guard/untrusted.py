"""Label text from outside Bleep as data, so the model never reads it as an instruction."""

import re

SOURCE = re.compile(r"[a-z0-9_.:-]{1,40}")
# `<untrusted`, `</untrusted`, `< / UNTRUSTED` and the fullwidth `＜`, with any whitespace between.
TAG_OPEN = re.compile(r"[<\uff1c](?=\s*/?\s*untrusted)", re.IGNORECASE)


def wrap_untrusted(source: str, text: str) -> str:
    """Return `text` inside `<untrusted source="…">`, with no way for it to close the tag early.

    Every `<` that starts an `untrusted` tag in the text (any case, any whitespace) becomes
    `&lt;`; other angle brackets are left alone so logs stay readable. `source` must match
    `[a-z0-9_.:-]{1,40}` so it cannot break out of the attribute.
    """
    if not SOURCE.fullmatch(source):
        raise ValueError(f"invalid untrusted source label: {source!r}")
    safe = TAG_OPEN.sub("&lt;", text)
    return f'<untrusted source="{source}">\n{safe}\n</untrusted>'
