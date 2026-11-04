"""The settings a person may change: model choice, budgets, thresholds, hosts' and services'
rules, URLs and protected containers. Never a secret, in either direction.

The body is read as plain JSON and checked for secrets before it is validated, so a refusal names
where the secret was without sending its value back, as a validation error would.

A read carries the overrides' `version` (in the body and as the `ETag`), and a change must send it
back in `If-Match`: 428 without it, 409 with the current version when the overrides have changed
since, so a stale page or a second writer never silently reverts a change.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.api.deps import get_settings as app_settings
from bleep.config import Settings
from bleep.incidents.settings_store import SettingsRefused, SettingsStale, check_body
from bleep.schemas.settings import SecretStatus, SettingsUpdate, SettingsView
from bleep.wiring import AppParts

router = APIRouter(tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]

MODEL_KEY_ENV = "OPENROUTER_API_KEY"  # `ModelSettings.openrouter_api_key`
# A settings, plugin or prompt body is a few thousand characters (a prompt override at most
# 20,000); the cap keeps the secret check, which reads the whole body, a small cost.
MAX_BODY_CHARS = 100_000


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
    settings: Annotated[Settings, Depends(app_settings)],
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SettingsView | JSONResponse:
    """Each section given replaces that section's overrides; see `SettingsUpdate`. `If-Match`
    holds the version the body was built from. `models` and `budgets` are a 403 while the dev
    panel, which edits them, is off."""
    if if_match is None or not if_match.strip():
        raise HTTPException(428, "send the settings version you read in If-Match")
    update = parse_body(body, SettingsUpdate)
    if not settings.dev_panel and (update.models is not None or update.budgets is not None):
        raise HTTPException(
            403,
            "the models and the budgets are changed in the dev panel, which is off; "
            "set BLEEP_DEV_PANEL=true to change them",
        )
    try:
        await parts.settings_store.update(update, version=version_of(if_match))
    except SettingsStale as stale:
        return stale_response(stale.current, "the settings changed since this version was read")
    except SettingsRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    view = settings_view(parts)
    response.headers["ETag"] = f'"{view.version}"'
    return view


def version_of(if_match: str) -> str:
    """The version in an `If-Match` header: an ETag, quoted, or the bare version."""
    return if_match.strip().removeprefix("W/").strip('"')


def stale_response(current: str, what: str) -> JSONResponse:
    """409: nothing was saved; the body and the `ETag` carry the version now, `current`."""
    return JSONResponse(
        {"detail": f"{what}; nothing was saved", "version": current},
        status_code=409,
        headers={"ETag": f'"{current}"'},
    )


def check_size(body: Any) -> None:
    """413 for a body over `MAX_BODY_CHARS` as JSON, before the secret check reads all of it; 422
    for one nested too deeply to measure. `BodyLimitMiddleware` caps the raw bytes earlier."""
    try:
        size = len(json.dumps(body, ensure_ascii=False))
    except RecursionError:
        raise HTTPException(422, "the body is nested too deeply") from None
    if size > MAX_BODY_CHARS:
        raise HTTPException(413, f"the body is over {MAX_BODY_CHARS:,} characters")


def parse_body[T](body: Any, model: type[T]) -> T:
    """`body` as `model`, or 413 when it is too large, or 422: for a secret anywhere in it, and
    for an invalid body with the reasons but never the values sent."""
    check_size(body)
    try:
        check_body(body)
        return model.model_validate(body)  # type: ignore[attr-defined]
    except SettingsRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    except ValidationError as error:
        detail = error.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(422, detail) from None
