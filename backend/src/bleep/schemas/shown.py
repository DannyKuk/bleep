"""A fixed text Bleep's code writes for a person, kept as the templates it is made of (#427).

Such a text is stored in English, which is also what any model reads of it (a notice goes into
the report's facts and the chat's snapshot), and shown in the person's language where it is
shown: the API's views and the values of a Telegram message. `prompts.show` renders it; its
templates are `prompts/shown/<name>.md`, each with a twin in every other language.
"""

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field


class Shown(BaseModel):
    """One template (`name`, under `prompts/shown/`) and its values, or, with no name, `parts`
    joined by `sep`. A value is a plain string (a name, a number, English text from elsewhere)
    or a shown text of its own."""

    name: str = ""
    values: dict[str, str | Shown] = Field(default_factory=dict)
    parts: list[Shown] = Field(default_factory=list)
    sep: str = " "

    @classmethod
    def of(cls, name: str, /, **values: str | Shown) -> Shown:
        return cls(name=name, values=values)

    @classmethod
    def joined(cls, parts: Iterable[Shown], sep: str = " ") -> Shown:
        return cls(parts=list(parts), sep=sep)

    def then(self, *more: Shown) -> Shown:
        """This text with `more` after it, each after a space."""
        return Shown.joined([self, *more])

    def stored(self) -> dict[str, Any]:
        """The shape kept in an event's payload, as `shown`, next to its English `text`."""
        return self.model_dump(exclude_defaults=True)

    @classmethod
    def in_payload(cls, payload: dict[str, Any]) -> Shown | None:
        """The shown text an event's payload keeps; None for one stored before #427."""
        shown = payload.get("shown")
        return cls.model_validate(shown) if isinstance(shown, dict) else None
