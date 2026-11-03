"""The plugins: listed with their settings and which secrets are set, configured, switched."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException

from bleep.api.deps import get_parts
from bleep.api.settings import parse_body
from bleep.incidents.settings_store import SettingsRefused
from bleep.plugins.registry import PluginError, UnknownPlugin
from bleep.schemas.plugins import PluginUpdate, PluginView
from bleep.wiring import AppParts

router = APIRouter(tags=["plugins"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/plugins")
async def list_plugins(parts: Parts) -> list[PluginView]:
    """Every plugin, with health for those switched on; a secret is only ever "set" or not."""
    return [await parts.plugins.view(name) for name in parts.plugins.names()]


@router.put("/plugins/{name}")
async def put_plugin(name: str, body: Annotated[Any, Body()], parts: Parts) -> PluginView:
    """Change the plugin's settings, switch it on or off, or both."""
    if name not in parts.plugins.names():
        raise HTTPException(404, "no such plugin")
    update = parse_body(body, PluginUpdate)
    try:
        await parts.settings_store.set_plugin(name, update)
    except UnknownPlugin:
        raise HTTPException(404, "no such plugin") from None
    except (PluginError, SettingsRefused) as refusal:
        raise HTTPException(422, str(refusal)) from None
    return await parts.plugins.view(name)
