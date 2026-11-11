"""The fixed texts Bleep puts on an incident's timeline, in English and as their templates (#427).

The graph, the runner, the watcher and the bot publish a notice through `payload`, so the event
keeps the English text every reader of the store gets (the report's facts, the chat's snapshot)
and the `shown` text the API and the notifier render in the person's language.
"""

from typing import Any

from bleep import prompts
from bleep.schemas.shown import Shown


def english(text: Shown) -> str:
    """`text` as it is stored and as a model reads it."""
    return prompts.show(text, prompts.LANGUAGES[0])


def shown_payload(text: Shown, **more: Any) -> dict[str, Any]:
    """The payload of an event that carries `text` (a `notice`, an `expected`)."""
    return {"text": english(text), "shown": text.stored(), **more}
