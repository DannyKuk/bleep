"""What the API takes and gives for the hosts added on the dashboard (#194). A private key is a
`SecretStr` on the way in and never on the way out; a host key is never sent by the page at all:
Bleep fetches it, shows its fingerprint, and stores it only when that fingerprint comes back."""

import ipaddress
import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr

HOST_ID = r"[a-z0-9][a-z0-9-]{0,39}"
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_SOCKET_PART = re.compile(r"[A-Za-z0-9_.-]{1,255}")


def _address(value: str) -> str:
    """A host name or an IP literal, and nothing an SSH client could read as more: no scheme,
    user, port, path, space or option."""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    labels = value.removesuffix(".").split(".")
    if len(value) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        raise ValueError("must be a host name or an IP address, such as box.lan or 10.0.0.5")
    return value.lower()


def _user(value: str) -> str:
    if not _USER.fullmatch(value):
        raise ValueError("must be a login name: letters, digits, _ . -, not starting with - or .")
    return value


def _docker_socket(value: str) -> str:
    parts = value.split("/")
    if (
        len(value) > 255
        or parts[0] != ""
        or not all(_SOCKET_PART.fullmatch(p) and p not in (".", "..") for p in parts[1:])
    ):
        raise ValueError("must be an absolute path on the host, such as /var/run/docker.sock")
    return value


def _name(value: str) -> str:
    value = value.strip()
    if not value or any(unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"} for c in value):
        raise ValueError("must be a short name without control characters")
    return value


Address = Annotated[str, AfterValidator(_address)]
Port = Annotated[int, Field(ge=1, le=65535)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostKeyRequest(Strict):
    """`POST /api/settings/hosts/host-key`: fetch the host key an SSH server offers."""

    address: Address
    port: Port = 22


class HostKeyOffer(BaseModel):
    """The host key Bleep was offered, to be confirmed by its fingerprint. `offer` names it when
    the host is saved; it expires after ten minutes and is used once."""

    offer: str
    address: str
    port: int
    fingerprint: str  # SHA256:..., as `ssh-keygen -lf` prints it on the host
    key_type: str


class HostKeyConfirmation(Strict):
    """The offer a person confirmed and the fingerprint they were shown."""

    offer: str = Field(max_length=64)
    fingerprint: str = Field(max_length=128)


class DashboardHostFields(Strict):
    name: Annotated[str, Field(max_length=80), AfterValidator(_name)]
    address: Address
    port: Port = 22
    user: Annotated[str, AfterValidator(_user)]
    docker_socket: Annotated[str, AfterValidator(_docker_socket)] = "/var/run/docker.sock"


class NewDashboardHost(DashboardHostFields):
    """`POST /api/settings/hosts`: everything at once, so nothing is saved before the host key
    is confirmed."""

    id: Annotated[str, Field(pattern=f"^{HOST_ID}$")]
    ssh_key: SecretStr
    host_key: HostKeyConfirmation


class DashboardHostEdit(DashboardHostFields):
    """`PUT /api/settings/hosts/{id}`, with the host's `version` in `If-Match`. A new address, port
    or user clears the key and the host key unless new ones come with it; `ssh_key` or `host_key`
    alone replaces that one for the connection the version names."""

    ssh_key: SecretStr | None = None
    host_key: HostKeyConfirmation | None = None
