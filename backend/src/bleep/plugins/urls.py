"""Where a plugin may send its token: the rule `COOLIFY_URL` and `MCP_URL` both follow.

A URL is accepted when it is http(s) with a host and no user, password, query or fragment, and
https unless its host is `_nearby`. See docs/design/plugins.md.
"""

import ipaddress
import re
import socket

import httpx

# The addresses a token may travel to over plain http: loopback, private and CGNAT (Tailscale).
NEARBY = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)
# A single-label name, such as a Docker service; one starting with a digit could be an address.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")


class UrlRefused(ValueError):
    """A plugin's URL is not one Bleep sends its token to; the text names the variable, never
    the URL."""


def _nearby(host: str) -> bool:
    """A host whose traffic stays on this machine or its network: an address in `NEARBY`, or a
    single-label name such as a Docker service. No DNS lookup is made: where a name resolves
    (Docker's DNS, else the host's search domains) is the operator's to set up."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return bool(_NAME.fullmatch(host)) and not _inet_aton(host)
    return any(ip in net for net in NEARBY if net.version == ip.version)


def _inet_aton(host: str) -> bool:
    """Whether the C library would read `host` as an IPv4 address (`134744072`, `0x8080808`)."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def check_url(raw: str, env: str, refused: type[UrlRefused] = UrlRefused) -> str:
    """`raw`, read from the variable `env`, as a URL Bleep may send a token to, or `refused`."""
    try:
        url = httpx.URL(raw.strip())
    except httpx.InvalidURL:
        raise refused(f"{env} is not a URL") from None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise refused(f"{env} must be an http(s) URL with a host and no password")
    if url.query or url.fragment or "?" in raw or "#" in raw:
        raise refused(f"{env} must have no query or fragment")
    if url.scheme == "http" and not _nearby(url.host):
        raise refused(
            f"{env} must use https unless it is a private address or a Docker service name,"
            " since the token travels with every request"
        )
    return str(url)
