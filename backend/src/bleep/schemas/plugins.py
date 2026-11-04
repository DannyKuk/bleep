"""What the API says about plugins. No field here ever holds a secret's value."""

from typing import Any

from pydantic import BaseModel


class PluginHealth(BaseModel):
    ok: bool
    detail: str = ""


class PluginSecret(BaseModel):
    """A secret the plugin uses: the environment variable's name and whether it is set."""

    env: str
    set: bool
    required: bool = True  # an optional one is used when set (the MCP plugin's MCP_TOKEN)


class PluginView(BaseModel):
    name: str
    title: str
    enabled: bool  # on and running: its tools and actions are in use
    # The person's toggle as stored. On while `enabled` is off, the plugin is waiting: for a
    # required secret not set in the env (see `secrets`), or for code that failed to build.
    switched_on: bool
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
