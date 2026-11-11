"""The plugins: listed with their settings and which secrets are set, configured, switched."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header

from bleep.api.deps import get_parts
from bleep.api.errors import Refusal
from bleep.api.settings import parse_body, stale, version_of
from bleep.incidents.settings_store import SettingsRefused, SettingsStale
from bleep.plugins.registry import PluginError, UnknownPlugin
from bleep.schemas.plugins import PluginUpdate, PluginView
from bleep.wiring import AppParts

router = APIRouter(tags=["plugins"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/plugins")
async def list_plugins(parts: Parts) -> list[PluginView]:
    """Every plugin, with health for those switched on; a secret is only ever "set" or not. The
    health checks run together, each bounded by its own timeout."""
    views = (parts.settings_store.plugin_view(name) for name in parts.plugins.names())
    return list(await asyncio.gather(*views))


@router.put("/plugins/{name}", response_model=PluginView)
async def put_plugin(
    name: str,
    body: Annotated[Any, Body()],
    parts: Parts,
    if_match: Annotated[str | None, Header()] = None,
) -> PluginView:
    """Change the plugin's settings, switch it on or off, or both. `If-Match` holds the
    plugin's `version` the body was built from: 428 without it, 409 when it is stale."""
    if name not in parts.plugins.names():
        raise Refusal(404, "no_plugin")
    if if_match is None or not if_match.strip():
        raise Refusal(428, "need_version_plugin")
    update = parse_body(body, PluginUpdate)
    try:
        await parts.settings_store.set_plugin(name, update, version=version_of(if_match))
    except SettingsStale as changed:
        raise stale(changed.current, "stale_plugin") from None
    except UnknownPlugin:
        raise Refusal(404, "no_plugin") from None
    except (PluginError, SettingsRefused) as refusal:
        raise Refusal.of(422, refusal) from None
    return await parts.settings_store.plugin_view(name)
