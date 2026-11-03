"""The settings a person may change: model choice, budgets, thresholds, hosts' and services'
rules, URLs and protected containers. Never a secret, in either direction.

The body is read as plain JSON and checked for secrets before it is validated, so a refusal names
where the secret was without sending its value back, as a validation error would.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.incidents.settings_store import SettingsRefused, check_body
from bleep.schemas.settings import SettingsUpdate, SettingsView
from bleep.wiring import AppParts

router = APIRouter(tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]


def settings_view(parts: AppParts) -> SettingsView:
    store = parts.settings_store
    return SettingsView(
        models=store.model_choice(),
        budgets=store.budgets(),
        thresholds=store.thresholds(),
        hosts=store.host_views(),
        plugins=store.plugin_toggles(),
        overrides=store.overrides().model_dump(mode="json", exclude_none=True),
    )


@router.get("/settings")
async def get_settings(parts: Parts) -> SettingsView:
    return settings_view(parts)


@router.put("/settings")
async def put_settings(body: Annotated[Any, Body()], parts: Parts) -> SettingsView:
    """Each section given replaces that section's overrides; see `SettingsUpdate`."""
    update = parse_body(body, SettingsUpdate)
    try:
        await parts.settings_store.update(update)
    except SettingsRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    return settings_view(parts)


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
