"""The secrets and plugin addresses set from the dashboard: write-only.

A read says whether each is set and where it comes from (the env or the dashboard); of an address
set on the dashboard it also gives the origin (scheme, host, port) and an opaque version, and of
one the env sets nothing more. A write sets or clears one and answers the same list, since a new
address clears the token it carries; a token is stored only with the version of the address it
was typed for. A secret is never sent back, not in an answer, an error or a log. See
docs/design/settings.md.
"""

import logging
from collections.abc import Awaitable
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from bleep.api.deps import get_parts
from bleep.api.settings import check_size
from bleep.notify.telegram import mask_chat_id
from bleep.plugins.urls import UrlRefused, check_dashboard_url, check_public, check_url
from bleep.schemas.secrets import SecretValue, SecretView
from bleep.store.secrets import (
    AddressStale,
    Kind,
    SecretFromEnv,
    SecretName,
    SecretNotSaved,
    SecretRefused,
    SecretStore,
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
    clears its token, and a plugin's is switched off until it is switched on again."""
    entry = _entry(parts.secrets, name)
    check_size(body)
    try:
        sent = SecretValue.model_validate(body)
    except ValidationError as error:
        detail = error.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(422, detail) from None
    value = sent.value
    if entry.kind == "address":
        if entry.private:
            value = _model_address(name, value)
        else:
            value = await _dashboard_address(parts, name, value)
        current = parts.secrets.get(name)
        owned = parts.secrets.state(name).env_owned
        if not owned and current and _same_address(current.get_secret_value(), value):
            return secret_views(parts.secrets)  # spelled differently, the same address
    await _change(parts.secrets.set(name, value, address_version=sent.address_version))
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
                version=store.address_version(entry.name),
                invalid=_invalid(store, entry.name, entry.kind),
                private=entry.private,
            )
        )
    return views


def _shown(store: SecretStore, name: str, kind: Kind) -> str | None:
    """A secret never; an id masked; an address only when set on the dashboard, and then only its
    origin: a path can hold a credential (a hosted MCP server's often does), and the env's
    address was never the API's to show."""
    value = store.get(name)
    if value is None or kind == "secret":
        return None
    if kind == "id":
        return mask_chat_id(value.get_secret_value())
    if store.state(name).env_owned or _invalid(store, name, kind):
        return None
    try:
        url = httpx.URL(value.get_secret_value())
    except httpx.InvalidURL:  # a file edited by hand: set, but not shown, and never quoted
        return None
    if not url.scheme or not url.host:
        return None
    host = f"[{url.host}]" if ":" in url.host else url.host
    return f"{url.scheme}://{host}" + (f":{url.port}" if url.port is not None else "")


async def _dashboard_address(parts: AppParts, name: str, value: str) -> str:
    """`value` as an address the dashboard may set: https, and resolving to public addresses
    only (checked again at every connection, `plugins/urls.PublicOnlyTransport`). Plain http and
    private networks are for `.env`: a dashboard session must not aim a plugin's client at the
    Docker API or another service on Bleep's own networks."""
    try:
        value = check_dashboard_url(value, name)
    except UrlRefused as refused:
        raise HTTPException(422, str(refused)) from None
    url = httpx.URL(value)
    try:
        await check_public(url.host, url.port or 443, parts.plugins.resolver)
    except ValueError as refusal:
        raise HTTPException(
            422,
            f"{name} set on the dashboard must reach public addresses only, and {refusal}; "
            "an address on a private network can be set in .env",
        ) from None
    return value


def _model_address(name: str, value: str) -> str:
    """`value` as a model server's address (`LOCAL_BASE_URL`, `EMBEDDINGS_BASE_URL`): held to
    the rule of an address in `.env`, so it may be private, a Docker name or
    `host.docker.internal`, where a local model usually listens. It is still bound to the key
    it carries, cleared when it changes."""
    try:
        return check_url(value, name, internal=True)
    except UrlRefused as refused:
        raise HTTPException(422, str(refused)) from None


def _invalid(store: SecretStore, name: str, kind: Kind) -> bool:
    """A dashboard address that the API would refuse now, as `PUT` checks it before resolving."""
    value = store.get(name)
    if kind != "address" or value is None or store.state(name).env_owned:
        return False
    try:
        if store.entry(name).private:
            check_url(value.get_secret_value(), name, internal=True)
        else:
            check_dashboard_url(value.get_secret_value(), name)
    except UrlRefused:
        return True
    return False


def _same_address(old: str, new: str) -> bool:
    """Whether two accepted addresses name the same endpoint: httpx already folds the case of
    the scheme and host and drops a default port; an empty path is `/`. A stored address that
    does not parse (a file edited by hand) is never the same."""
    try:
        a, b = httpx.URL(old), httpx.URL(new)
    except httpx.InvalidURL:
        return False
    return (a.scheme, a.host, a.port, a.raw_path) == (b.scheme, b.host, b.port, b.raw_path)


def _entry(store: SecretStore, name: str) -> SecretName:
    """A name `GET /api/secrets` lists; a host's key (a family name) is set only with its host."""
    if name not in {entry.name for entry in store.names()}:
        raise HTTPException(404, "no such secret can be set from the dashboard")
    return store.entry(name)


async def _change(change: Awaitable[set[str]]) -> None:
    try:
        await change
    except SecretFromEnv as owned:
        raise HTTPException(409, str(owned)) from None
    except AddressStale as stale:
        raise HTTPException(428 if stale.missing else 409, str(stale)) from None
    except SecretRefused as refused:
        raise HTTPException(422, str(refused)) from None
    except SecretNotSaved as not_saved:
        raise HTTPException(500, str(not_saved)) from None
    except OSError as error:
        log.error("secrets: the secret file could not be written (%s)", type(error).__name__)
        raise HTTPException(500, "the secret could not be saved") from None
