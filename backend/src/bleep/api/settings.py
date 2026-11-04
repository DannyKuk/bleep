"""The settings a person may change: model choice, budgets, thresholds, hosts' and services'
rules, URLs and protected containers. Never a secret, in either direction.

The body is read as plain JSON and checked for secrets before it is validated, so a refusal names
where the secret was without sending its value back, as a validation error would.

A read carries the overrides' `version` (in the body and as the `ETag`), and a change must send it
back in `If-Match`: 428 without it, 409 with the current version when the overrides have changed
since, so a stale page or a second writer never silently reverts a change.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.incidents.settings_store import SettingsRefused, SettingsStale, check_body
from bleep.schemas.settings import SecretStatus, SettingsUpdate, SettingsView
from bleep.wiring import AppParts

router = APIRouter(tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]

MODEL_KEY_ENV = "OPENROUTER_API_KEY"  # `ModelSettings.openrouter_api_key`


def settings_view(parts: AppParts) -> SettingsView:
    store = parts.settings_store
    return SettingsView(
        models=store.model_choice(),
        model_key=SecretStatus(
            env=MODEL_KEY_ENV, set=store.models().openrouter_api_key is not None
        ),
        budgets=store.budgets(),
        thresholds=store.thresholds(),
        hosts=store.host_views(),
        plugins=store.plugin_toggles(),
        overrides=store.overrides().model_dump(mode="json", exclude_none=True),
        version=store.version(),
    )


@router.get("/settings")
async def get_settings(parts: Parts, response: Response) -> SettingsView:
    view = settings_view(parts)
    response.headers["ETag"] = f'"{view.version}"'
    return view


@router.put("/settings", response_model=SettingsView)
async def put_settings(
    body: Annotated[Any, Body()],
    parts: Parts,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsView | JSONResponse:
    """Each section given replaces that section's overrides; see `SettingsUpdate`. `If-Match`
    holds the version the body was built from."""
    if if_match is None or not if_match.strip():
        raise HTTPException(428, "send the settings version you read in If-Match")
    update = parse_body(body, SettingsUpdate)
    try:
        await parts.settings_store.update(update, version=_version_of(if_match))
    except SettingsStale as stale:
        return JSONResponse(
            {
                "detail": "the settings changed since this version was read; nothing was saved",
                "version": stale.current,
            },
            status_code=409,
            headers={"ETag": f'"{stale.current}"'},
        )
    except SettingsRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    view = settings_view(parts)
    response.headers["ETag"] = f'"{view.version}"'
    return view


def _version_of(if_match: str) -> str:
    """The version in an `If-Match` header: an ETag, quoted, or the bare version."""
    return if_match.strip().removeprefix("W/").strip('"')


def parse_body[T](body: Any, model: type[T]) -> T:
    """`body` as `model`, or 422: for a secret anywhere in it, and for an invalid body with the
    reasons but never the values sent."""
    try:
        check_body(body)
        return model.model_validate(body)  # type: ignore[attr-defined]
    except SettingsRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    except ValidationError as error:
        detail = error.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(422, detail) from None
