"""What an error carries so the API can say it in the person's language (#456).

An error's own text (`str(error)`) stays English: it is what a log line holds and what anything
that stores or reads it sees. An error that reaches a person also carries the name of its
template under `prompts/api/` and the template's values; the API renders those
(`api/errors.py`).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from bleep.schemas.shown import Shown


@dataclass(frozen=True)
class Problem:
    """One field's problem, as pydantic reports it: the field's path, the error type, its
    English message and the context values the type's template is filled with."""

    loc: tuple[str | int, ...]
    type: str
    msg: str
    ctx: Mapping[str, str]


class Templated:
    """Mixin for an exception that has a template for the API (`prompts/api/<template>.md`)
    and the values it is filled with. A value is plain text, a count (shown with the language's
    thousands separator, `100,000` or `100.000`; a number such as a port goes in as text), a shown
    text, another templated error or a tuple of `Problem`s. With no template the API says
    `str(error)`."""

    template: str
    values: Mapping[str, Value]

    def __init__(self, message: str, template: str = "", **values: Value) -> None:
        super().__init__(message)
        self.template = template
        self.values = values


type Value = str | int | Shown | Templated | tuple[Problem, ...]
