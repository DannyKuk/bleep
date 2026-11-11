"""Hosts added, changed and removed on the dashboard (#194). The hosts file's hosts are read-only
here. A private key goes in and never comes out: a host shows its key and its confirmed host key
only as set or not. Adding a host takes two calls: fetch the host key, then save the host with
the fingerprint the person confirmed. A change and a removal name the host's `version` in
`If-Match`, so a key is stored only with the connection it was typed for. See
docs/design/hosts.md.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Response
from pydantic import BaseModel, ValidationError

from bleep.api.deps import get_parts
from bleep.api.errors import Refusal, problems_of
from bleep.api.settings import check_size, stale, version_of
from bleep.hosts.dashboard import (
    HostConflict,
    HostKeyBusy,
    HostKeyUnavailable,
    HostRefused,
    HostStale,
    UnknownHost,
)
from bleep.schemas.dashboard_hosts import (
    DashboardHostEdit,
    HostKeyConfirmation,
    HostKeyOffer,
    HostKeyRequest,
    NewDashboardHost,
)
from bleep.schemas.refusal import Problem
from bleep.schemas.settings import HostSettingsView
from bleep.store.secrets import SecretNotSaved, SecretRefused
from bleep.wiring import AppParts

router = APIRouter(prefix="/settings/hosts", tags=["settings"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.post("/host-key")
async def fetch_host_key(body: Annotated[Any, Body()], parts: Parts) -> HostKeyOffer:
    """The host key the SSH server at the address offers, to confirm by its fingerprint before
    the host is saved. Nothing is stored. One sentence for every failure (502), whatever the
    cause, and one fetch at a time (429)."""
    request = _parsed(body, HostKeyRequest)
    try:
        return await parts.dashboard_hosts.offer_host_key(request.address, request.port)
    except HostKeyUnavailable as unavailable:
        raise Refusal.of(502, unavailable) from None
    except HostKeyBusy as busy:
        raise Refusal.of(429, busy) from None


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
) -> HostSettingsView:
    edit = _parsed(body, DashboardHostEdit)
    version = _required(if_match)
    try:
        await _change(parts.dashboard_hosts.change(host_id, edit, version=version))
    except HostStale as changed:
        raise stale(changed.current, "stale_host") from None
    return _view(parts, host_id)


@router.delete("/{host_id}", status_code=204, response_model=None)
async def remove_host(
    host_id: str, parts: Parts, if_match: Annotated[str | None, Header()] = None
) -> Response:
    """Remove a dashboard host and its keys; its open incidents are closed with a notice."""
    version = _required(if_match)
    try:
        await _change(parts.dashboard_hosts.remove(host_id, version=version))
    except HostStale as changed:
        raise stale(changed.current, "stale_host") from None
    return Response(status_code=204)


# Every field name of the host bodies: a 422 names these and nothing else the client sent.
FIELDS = frozenset(
    name
    for model in (HostKeyRequest, NewDashboardHost, DashboardHostEdit, HostKeyConfirmation)
    for name in model.model_fields
)


def _parsed[T: BaseModel](body: Any, model: type[T]) -> T:
    """The body as `model`, or 422 with where and why but never a value sent, nor a key of the
    client's own: a field Bleep does not know is named `<unknown field>`, since a pasted key could
    arrive as a JSON key."""
    check_size(body)
    try:
        return model.model_validate(body)
    except ValidationError as error:
        problems = [
            Problem(
                loc=tuple(_where(part) for part in problem.loc),
                type=problem.type,
                msg=problem.msg,
                ctx=problem.ctx,
            )
            for problem in problems_of(error)
        ]
        raise Refusal.invalid(problems) from None


def _where(part: int | str) -> int | str:
    return part if isinstance(part, int) or part in FIELDS else "<unknown field>"


def _required(if_match: str | None) -> str:
    if if_match is None or not if_match.strip():
        raise Refusal(428, "need_version_host")
    return version_of(if_match)


async def _change(change: Any) -> None:
    try:
        await change
    except UnknownHost:
        raise Refusal(404, "no_dashboard_host") from None
    except HostConflict as conflict:
        raise Refusal.of(409, conflict) from None
    except (HostRefused, SecretRefused) as refused:
        raise Refusal.of(422, refused) from None
    except SecretNotSaved as not_saved:
        raise Refusal.of(500, not_saved) from None


def _view(parts: AppParts, host_id: str) -> HostSettingsView:
    return next(view for view in parts.settings_store.host_views() if view.id == host_id)
