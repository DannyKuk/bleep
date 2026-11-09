"""The settings a person may change: model choice, budgets, thresholds, the time zone, hosts' and
services' rules, URLs and protected containers. Never a secret, in either direction.

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
from bleep.hosts.coolify_names import coolify_slug
from bleep.incidents.settings_store import SettingsRefused, SettingsStale, check_body
from bleep.schemas.settings import MODEL_KEY_ENV, SecretStatus, SettingsUpdate, SettingsView
from bleep.wiring import AppParts

router = APIRouter(tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]

# A settings, plugin or prompt body is a few thousand characters (a prompt override at most
# 20,000); the cap keeps the secret check, which reads the whole body, a small cost.
MAX_BODY_CHARS = 100_000
# The deepest body the dashboard sends is about six levels (settings > services > key > urls >
# item). A fixed limit keeps the answer the same on every platform: `json.dumps` alone raises
# RecursionError at a depth set by the thread's stack size, which differs between macOS and Linux.
MAX_BODY_DEPTH = 32


def _too_deep(body: Any) -> bool:
    pending: list[tuple[Any, int]] = [(body, 1)]
    while pending:
        value, depth = pending.pop()
        if not isinstance(value, dict | list):
            continue
        if depth > MAX_BODY_DEPTH:
            return True
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in children)
    return False


def settings_view(parts: AppParts) -> SettingsView:
    store = parts.settings_store
    return SettingsView(
        models=store.model_choice(),
        model_key=SecretStatus(
            env=MODEL_KEY_ENV,
            set=store.models().openrouter_api_key is not None,
            source=parts.secrets.state(MODEL_KEY_ENV).source,
        ),
        budgets=store.budgets(),
        thresholds=store.thresholds(),
        hosts=store.host_views(),
        plugins=store.plugin_toggles(),
        timezone=store.timezone(),
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
    refuse_names_without_a_slug(update)
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


def refuse_names_without_a_slug(update: SettingsUpdate) -> None:
    """422 for a protected name with no letter or digit (`🚀`): Coolify's slug of it is empty,
    so it would be stored, shown as protected and match no application. A Docker container name
    always starts with a letter or digit, so no real container is refused."""
    for host_id, host in (update.hosts or {}).items():
        for service, overrides in host.services.items():
            for name in overrides.protected_containers or []:
                if not coolify_slug(name):
                    raise HTTPException(
                        422,
                        f"host {host_id}, service {service}: protected entry {name!r} has no "
                        "letters or digits for Coolify's slug, so it would protect nothing by "
                        "a Coolify name; protect the application by its uuid",
                    )


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
    for one nested deeper than `MAX_BODY_DEPTH`. `BodyLimitMiddleware` caps the raw bytes
    earlier."""
    if _too_deep(body):
        raise HTTPException(422, "the body is nested too deeply")
    try:
        size = len(json.dumps(body, ensure_ascii=False))
    except RecursionError:
        raise HTTPException(422, "the body is nested too deeply") from None
    except TypeError:  # not JSON (a form or text/plain body arrives as bytes)
        raise HTTPException(422, "send the body as JSON") from None
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
