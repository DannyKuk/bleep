"""The API's errors in the person's language (#456).

A route raises `Refusal` with the name of a template under `prompts/api/` and its values, or
`Refusal.of` an error that carries them (`schemas.refusal.Templated`). The handlers here render
it in the language picked in the settings, so no route looks the language up. Pydantic's own
messages are rendered from `prompts/api/problem/<error type>.md`, keyed by the error's type and
filled with its context; a type with no template keeps pydantic's English message. What an error
says in a log or stores stays English.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from bleep import prompts
from bleep.schemas.refusal import Problem, Templated, Value
from bleep.schemas.shown import Shown

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = prompts.LANGUAGES[0]
# The types whose message has its own template for a count of one: pydantic says "1 character".
SINGULAR = {
    "string_too_short": "min_length",
    "string_too_long": "max_length",
    "too_short": "min_length",
    "too_long": "max_length",
}


class Refusal(HTTPException):
    """An error the API answers with a text in the person's language. `extra` is more of the
    JSON body, such as a conflict's `version`."""

    def __init__(
        self,
        status_code: int,
        template: str,
        /,
        *,
        headers: dict[str, str] | None = None,
        extra: Mapping[str, Any] | None = None,
        fallback: str = "",
        **values: Value,
    ) -> None:
        english = render(template, values, fallback or template, DEFAULT_LANGUAGE)
        super().__init__(status_code, detail=english, headers=headers)
        self.template = template
        self.values = values
        self.fallback = fallback or english
        self.extra = dict(extra or {})
        self.problems: list[Problem] | None = None

    @classmethod
    def of(
        cls, status_code: int, error: Exception, headers: dict[str, str] | None = None
    ) -> Refusal:
        """`error`'s template and values, or its own English text when it carries none."""
        values = getattr(error, "values", {})
        return cls(
            status_code,
            getattr(error, "template", ""),
            headers=headers,
            fallback=str(error),
            **values,
        )

    @classmethod
    def invalid(cls, problems: Sequence[Problem]) -> Refusal:
        """422 with a list of problems, each rendered from its type."""
        refusal = cls(422, "invalid")
        refusal.problems = list(problems)
        return refusal

    def text_in(self, language: str) -> str:
        return render(self.template, self.values, self.fallback, language)


def problems_of(error: ValidationError) -> list[Problem]:
    """What a validation error says, without the input: where, its type, message and context."""
    return [
        Problem(
            loc=tuple(item["loc"]),
            type=item["type"],
            msg=item["msg"],
            ctx={key: str(value) for key, value in (item.get("ctx") or {}).items()},
        )
        for item in error.errors(include_input=False, include_url=False)
    ]


def render(template: str, values: Mapping[str, Value], fallback: str, language: str) -> str:
    """The template `api/<template>` in `language`; `fallback` when there is none or it no
    longer renders."""
    if not template:
        return fallback
    try:
        filled = {key: _value(value, language) for key, value in values.items()}
        return prompts.load_in(language, f"api/{template}", **filled).strip()
    except prompts.PromptError as error:
        log.warning("api: %s does not render in %s (%s)", template, language, error)
        return fallback


def _value(value: Value, language: str) -> str:
    match value:
        case str():
            return value
        case int():
            return _grouped(value, language)
        case Shown():
            return prompts.show(value, language)
        case Templated():
            return render(value.template, value.values, str(value), language)
        case _:
            return "; ".join(
                f"{'.'.join(str(part) for part in problem.loc) or 'value'}: "
                f"{problem_text(problem, language)}"
                for problem in value
            )


def _grouped(number: int, language: str) -> str:
    text = f"{number:,}"
    return text.replace(",", ".") if language == "de" else text


def problem_text(problem: Problem, language: str) -> str:
    """The problem's message from its type's template, else pydantic's own."""
    name = f"api/problem/{problem.type}"
    count = SINGULAR.get(problem.type)
    if count is not None and problem.ctx.get(count) == "1":
        name += "_one"
    try:
        needed = prompts.placeholders(prompts.file_text(name))
        values = {key: _ctx_value(problem.ctx[key], language) for key in needed}
        return prompts.load_in(language, name, **values).strip()
    except prompts.PromptError, KeyError:
        return problem.msg


def _ctx_value(value: str, language: str) -> str:
    """Pydantic joins the values a literal allows as `'a', 'b' or 'c'`, in English."""
    if language == "de" and " or " in value:
        head, _, last = value.rpartition(" or ")
        return f"{head} oder {last}"
    return value


def language_of(app: Any) -> str:
    """The language picked in the settings; English when there is no app with settings yet."""
    parts = getattr(getattr(app, "state", None), "parts", None)
    try:
        return parts.settings_store.language() if parts is not None else DEFAULT_LANGUAGE
    except Exception:  # an error answer must never fail for the language
        log.warning("api: the language could not be read", exc_info=True)
        return DEFAULT_LANGUAGE


async def _refusal(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, Refusal)
    language = language_of(request.app)
    if error.problems is not None:
        detail: Any = [
            {"type": p.type, "loc": list(p.loc), "msg": problem_text(p, language)}
            for p in error.problems
        ]
    else:
        detail = error.text_in(language)
    return JSONResponse(
        {"detail": detail, **error.extra}, status_code=error.status_code, headers=error.headers
    )


async def _invalid_request(request: Request, error: Exception) -> JSONResponse:
    """FastAPI's 422 for a body or parameter that does not fit, with each message from its
    type's template; the rest of each entry is as FastAPI sends it."""
    assert isinstance(error, RequestValidationError)
    language = language_of(request.app)
    detail = []
    for item in error.errors():
        ctx = item.get("ctx") or {}
        problem = Problem(
            loc=tuple(item["loc"]),
            type=item["type"],
            msg=item["msg"],
            ctx={key: str(value) for key, value in ctx.items()},
        )
        detail.append({**item, "msg": problem_text(problem, language)})
    return JSONResponse({"detail": jsonable_encoder(detail)}, status_code=422)


def register(app: FastAPI) -> None:
    app.add_exception_handler(Refusal, _refusal)
    app.add_exception_handler(RequestValidationError, _invalid_request)
