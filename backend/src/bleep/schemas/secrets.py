"""What the API says about the secrets and plugin addresses set from the dashboard. No field
here ever holds a secret's value."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SecretView(BaseModel):
    """One value the dashboard may set. `env_owned`: the environment decides it, so the dashboard
    can neither set nor clear it. `value` is shown for an address Bleep accepts and, masked, for
    an id; never for a secret."""

    name: str
    kind: Literal["secret", "address", "id"]
    set: bool
    source: Literal["env", "dashboard"] | None
    env_owned: bool
    carries: str | None = None  # an address: the token sent to it, cleared when it changes
    value: str | None = None


class SecretValue(BaseModel):
    """`PUT /api/secrets/{name}`."""

    model_config = ConfigDict(extra="forbid")

    value: str
