"""Hosts added, changed and removed on the dashboard (#194). The hosts file's hosts are read-only
here. A private key goes in and never comes out: a host shows its key and its confirmed host key
only as set or not. Adding a host takes two calls: fetch the host key, then save the host with
the fingerprint the person confirmed. A change and a removal name the host's `version` in
`If-Match`, so a key is stored only with the connection it was typed for. See
docs/design/hosts.md.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from bleep.api.deps import get_parts
from bleep.api.settings import check_size, stale_response, version_of
from bleep.hosts.dashboard import HostConflict, HostRefused, HostStale, UnknownHost
from bleep.hosts.ssh import SshUnavailable
from bleep.schemas.dashboard_hosts import (
    DashboardHostEdit,
    HostKeyOffer,
    HostKeyRequest,
    NewDashboardHost,
)
from bleep.schemas.settings import HostSettingsView
from bleep.store.secrets import SecretNotSaved, SecretRefused
from bleep.wiring import AppParts

router = APIRouter(prefix="/settings/hosts", tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.post("/host-key")
async def fetch_host_key(body: Annotated[Any, Body()], parts: Parts) -> HostKeyOffer:
    """The host key the SSH server at the address offers, to confirm by its fingerprint before
    the host is saved. Nothing is stored; 502 when no SSH server answers there."""
    request = _parsed(body, HostKeyRequest)
    try:
        return await parts.dashboard_hosts.offer_host_key(request.address, request.port)
    except SshUnavailable as unavailable:
        raise HTTPException(502, f"no host key: {_cause(unavailable)}") from None


@router.post("", status_code=201)
async def add_host(body: Annotated[Any, Body()], parts: Parts) -> HostSettingsView:
    new = _parsed(body, NewDashboardHost)
    await _change(parts.dashboard_hosts.add(new))
    return _view(parts, new.id)


@router.put("/{host_id}", response_model=HostSettingsView)
async def change_host(
    host_id: str,
    body: Annotated[Any, Body()],
    parts: Parts,
    if_match: Annotated[str | None, Header()] = None,
) -> HostSettingsView | JSONResponse:
    edit = _parsed(body, DashboardHostEdit)
    version = _required(if_match)
    try:
        await _change(parts.dashboard_hosts.change(host_id, edit, version=version))
    except HostStale as stale:
        return stale_response(stale.current, "the host changed since this version was read")
    return _view(parts, host_id)


@router.delete("/{host_id}", status_code=204, response_model=None)
async def remove_host(
    host_id: str, parts: Parts, if_match: Annotated[str | None, Header()] = None
) -> Response | JSONResponse:
    """Remove a dashboard host and its keys; its open incidents are closed with a notice."""
    version = _required(if_match)
    try:
        await _change(parts.dashboard_hosts.remove(host_id, version=version))
    except HostStale as stale:
        return stale_response(stale.current, "the host changed since this version was read")
    return Response(status_code=204)


def _parsed[T: BaseModel](body: Any, model: type[T]) -> T:
    """The body as `model`, or 422 with where and why but never a value sent: a key is one."""
    check_size(body)
    try:
        return model.model_validate(body)
    except ValidationError as error:
        detail = error.errors(include_input=False, include_url=False, include_context=False)
        raise HTTPException(422, detail) from None


def _required(if_match: str | None) -> str:
    if if_match is None or not if_match.strip():
        raise HTTPException(428, "send the host's version you read in If-Match")
    return version_of(if_match)


async def _change(change: Any) -> None:
    try:
        await change
    except UnknownHost:
        raise HTTPException(404, "no such host was added on the dashboard") from None
    except HostConflict as conflict:
        raise HTTPException(409, str(conflict)) from None
    except (HostRefused, SecretRefused) as refused:
        raise HTTPException(422, str(refused)) from None
    except SecretNotSaved as not_saved:
        raise HTTPException(500, str(not_saved)) from None


def _view(parts: AppParts, host_id: str) -> HostSettingsView:
    return next(view for view in parts.settings_store.host_views() if view.id == host_id)


def _cause(unavailable: SshUnavailable) -> str:
    cause = unavailable.cause
    if isinstance(cause, BaseException):
        return str(cause) or type(cause).__name__
    return cause
