"""What the API says about the secrets and plugin addresses set from the dashboard. No field
here ever holds a secret's value."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SecretView(BaseModel):
    """One value the dashboard may set. `env_owned`: the environment decides it, so the dashboard
    can neither set nor clear it. `value` is an address's origin (scheme, host, port) when it was
    set on the dashboard, and an id masked; never a secret, a path or an address from the env.
    `version` is an address's opaque version when it was set on the dashboard: a token is sent
    with the version of the address it was typed for."""

    name: str
    kind: Literal["secret", "address", "id"]
    set: bool
    source: Literal["env", "dashboard"] | None
    env_owned: bool
    carries: str | None = None  # an address: the token sent to it, cleared when it changes
    value: str | None = None
    version: str | None = None


class SecretValue(BaseModel):
    """`PUT /api/secrets/{name}`. A token an address carries needs `address_version`: the
    `version` of that address as read when the token was typed (428 without it, 409 when the
    address changed since)."""

    model_config = ConfigDict(extra="forbid")

    value: str
    address_version: str | None = None
