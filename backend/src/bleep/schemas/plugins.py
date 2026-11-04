"""What the API says about plugins. No field here ever holds a secret's value."""

from typing import Any

from pydantic import BaseModel


class PluginHealth(BaseModel):
    ok: bool
    detail: str = ""


class PluginSecret(BaseModel):
    """A secret the plugin needs: the environment variable's name and whether it is set."""

    env: str
    set: bool


class PluginView(BaseModel):
    name: str
    title: str
    enabled: bool
    settings: dict[str, Any]
    settings_schema: dict[str, Any]  # JSON schema of the plugin's settings, for the form
    secrets: list[PluginSecret]
    health: PluginHealth | None = None  # only for an enabled plugin
    version: str  # of the plugin's stored row; `PUT /api/plugins/{name}` sends it in `If-Match`


class PluginUpdate(BaseModel):
    """`PUT /api/plugins/{name}`: either part may be left out."""

    model_config = {"extra": "forbid"}

    enabled: bool | None = None
    settings: dict[str, Any] | None = None
