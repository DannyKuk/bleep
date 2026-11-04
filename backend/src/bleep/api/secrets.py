"""The secrets and plugin addresses set from the dashboard: write-only.

A read says only whether each is set and where it comes from (the env or the dashboard); a write
sets or clears one and answers the same list, since a new address clears the token it carries.
A value is never sent back, not in an answer, an error or a log. See docs/design/settings.md.
"""

import logging
from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.api.settings import check_size
from bleep.notify.telegram import mask_chat_id
from bleep.plugins.urls import UrlRefused, check_url
from bleep.schemas.secrets import SecretValue, SecretView
from bleep.store.secrets import (
    Kind,
    SecretFromEnv,
    SecretName,
    SecretRefused,
    SecretStore,
    UnknownSecret,
)
from bleep.wiring import AppParts

log = logging.getLogger(__name__)

router = APIRouter(tags=["secrets"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/secrets")
async def list_secrets(parts: Parts) -> list[SecretView]:
    return secret_views(parts.secrets)


@router.put("/secrets/{name}")
async def put_secret(name: str, body: Annotated[Any, Body()], parts: Parts) -> list[SecretView]:
    """Set one value. An address must be one Bleep sends a token to (`check_url`); a new one
    clears its token, and the plugin is switched off until it is switched on again."""
    entry = _entry(parts.secrets, name)
    check_size(body)
    try:
        value = SecretValue.model_validate(body).value
    except ValidationError as error:
        detail = error.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(422, detail) from None
    if entry.kind == "address":
        try:
            value = check_url(value, name)
        except UrlRefused as refused:
            raise HTTPException(422, str(refused)) from None
    await _change(parts.secrets.set(name, value))
    return secret_views(parts.secrets)


@router.delete("/secrets/{name}")
async def delete_secret(name: str, parts: Parts) -> list[SecretView]:
    """Clear one value set from the dashboard; one the env sets is a 409."""
    _entry(parts.secrets, name)
    await _change(parts.secrets.clear(name))
    return secret_views(parts.secrets)


def secret_views(store: SecretStore) -> list[SecretView]:
    views = []
    for entry in store.names():
        state = store.state(entry.name)
        views.append(
            SecretView(
                name=entry.name,
                kind=entry.kind,
                set=state.set,
                source=state.source,
                env_owned=state.env_owned,
                carries=entry.carries,
                value=_shown(store, entry.name, entry.kind),
            )
        )
    return views


def _shown(store: SecretStore, name: str, kind: Kind) -> str | None:
    value = store.get(name)
    if value is None or kind == "secret":
        return None
    if kind == "id":
        return mask_chat_id(value.get_secret_value())
    try:  # an address from the env may hold a password; only an accepted one is shown
        return check_url(value.get_secret_value(), name)
    except UrlRefused:
        return None


def _entry(store: SecretStore, name: str) -> SecretName:
    try:
        return store.entry(name)
    except UnknownSecret:
        raise HTTPException(404, "no such secret can be set from the dashboard") from None


async def _change(change: Awaitable[set[str]]) -> None:
    try:
        await change
    except SecretFromEnv as owned:
        raise HTTPException(409, str(owned)) from None
    except SecretRefused as refused:
        raise HTTPException(422, str(refused)) from None
    except OSError as error:
        log.error("secrets: the secret file could not be written (%s)", type(error).__name__)
        raise HTTPException(500, "the secret could not be saved") from None
