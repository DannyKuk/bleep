"""Where a plugin may send its token: the rule `COOLIFY_URL` and `MCP_URL` both follow.

A URL is accepted when it is http(s) with a host and no user, password, query or fragment, and
https unless its host is `_nearby`. An address set from the dashboard is held to more: https,
and every address its name resolves to public, checked again at every connection, which is then
made to the address checked (`PublicOnlyTransport`). See docs/design/plugins.md and
docs/design/settings.md.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable

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
# The names cloud metadata services answer at; a model server's address may never be one.
METADATA_NAMES = frozenset(
    {"metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"}
)
# Their addresses outside link-local: AWS's IPv6 endpoint and Alibaba Cloud's.
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("fd00:ec2::254", "100.100.100.200")
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


def _metadata(host: str) -> bool:
    """A cloud metadata service: one of its names (Google's and AWS's sit under `.internal`), a
    link-local address, where most answer (169.254.169.254), or one of `METADATA_ADDRESSES`."""
    if host.lower().rstrip(".") in METADATA_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_link_local or ip in METADATA_ADDRESSES


def _inet_aton(host: str) -> bool:
    """Whether the C library would read `host` as an IPv4 address (`134744072`, `0x8080808`)."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


def check_url(
    raw: str,
    env: str,
    refused: type[UrlRefused] = UrlRefused,
    *,
    https_hint: str = "",
    internal: bool = False,
) -> str:
    """`raw`, read from the variable `env`, as a URL Bleep may send a token to, or `refused`.
    `https_hint` follows the plain-http refusal and says where the https address is. With
    `internal`, a name under `.internal`, the top-level domain reserved for private networks, is
    nearby too: Docker names this machine `host.docker.internal`, where a local model server
    usually listens."""
    try:
        url = httpx.URL(raw.strip())
    except httpx.InvalidURL:
        raise refused(f"{env} is not a URL") from None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise refused(f"{env} must be an http(s) URL with a host and no password")
    if url.query or url.fragment or "?" in raw or "#" in raw:
        raise refused(f"{env} must have no query or fragment")
    if internal and _metadata(url.host):
        raise refused(f"{env} must not be a cloud metadata service")
    is_internal = internal and url.host.lower().endswith(".internal")
    if url.scheme == "http" and not (_nearby(url.host) or is_internal):
        raise refused(
            f"{env} must use https unless it is a private address or a Docker service name"
            f"{https_hint}, since the token travels with every request"
        )
    return str(url)


# A host's addresses: `(host, port)` to the IP addresses a connection could go to.
Resolver = Callable[[str, int], Awaitable[list[str]]]

# Ranges `is_global` counts as global that still lead somewhere else: NAT64 prefixes, which embed
# an IPv4 address of any kind.
_EMBEDDING = tuple(ipaddress.ip_network(net) for net in ("64:ff9b::/96", "64:ff9b:1::/48"))
# IPv6 global unicast: everything outside it (IPv4-compatible `::a.b.c.d`, SIIT `::ffff:0:...`,
# site-local `fec0::/10`) is refused, whatever `is_global` says of it.
_GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")


class AddressRefused(httpx.ConnectError):
    """A dashboard address is not https, cannot be resolved, or resolves to an address that is
    not public; the call fails as if the server could not be reached. The text never names the
    URL or the address."""


def is_public(raw: str) -> bool:
    """Whether a connection to `raw` leaves for the public internet: a global unicast address
    that does not embed another one (IPv4-mapped, 6to4, Teredo and NAT64 forms are judged by
    what they embed, or refused; an IPv6 address must be in 2000::/3). Private, loopback,
    link-local (cloud metadata), CGNAT, unique-local, site-local, unspecified, reserved and
    multicast addresses are not."""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public(str(ip.ipv4_mapped))
        if ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip not in _GLOBAL_UNICAST or any(ip in net for net in _EMBEDDING):
            return False
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """Every address the system resolver gives for `host` (an address resolves to itself)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(str(info[4][0]) for info in infos))
    return [host]


def check_dashboard_url(raw: str, env: str, refused: type[UrlRefused] = UrlRefused) -> str:
    """`check_url`, and https: plain http, which the rule allows to private addresses, is for an
    address set in the environment only."""
    url = check_url(raw, env, refused)
    if httpx.URL(url).scheme != "https":
        raise refused(
            f"{env} set on the dashboard must be https; an address on a private network or over "
            "plain http can be set in .env"
        )
    return url


async def check_public(host: str, port: int, resolver: Resolver) -> list[str]:
    """The addresses `host` resolves to, when there is at least one and every one is public;
    else `ValueError` in Bleep's words, never naming the host or an address."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]  # an address is never looked up
    except ValueError:
        addresses = None
    try:
        addresses = addresses if addresses is not None else await resolver(host, port)
    except OSError:
        raise ValueError("its name could not be resolved") from None
    if not addresses:
        raise ValueError("its name could not be resolved")
    if not all(is_public(address) for address in addresses):
        raise ValueError("it resolves to an address that is not public")
    return addresses


class PublicOnlyTransport(httpx.AsyncBaseTransport):
    """Sends a request only to a public address its host resolves to now, and to exactly that
    address: the name is resolved and checked at every request (so a name that resolves
    elsewhere later, DNS rebinding, or a redirect to another host is checked again), the request
    goes to the first checked address, and the name stays in the Host header and in TLS (SNI and
    the certificate check). Without `inner`, each request gets a transport of its own, closed
    with its answer, so one client closing never cuts off another's request on the same plugin."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None, resolver: Resolver) -> None:
        self._inner = inner
        self._resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme != "https":
            raise AddressRefused("the address is not https", request=request)
        try:
            addresses = await check_public(url.host, url.port or 443, self._resolver)
        except ValueError as refusal:
            raise AddressRefused(f"the address is refused: {refusal}", request=request) from None
        request.url = url.copy_with(host=addresses[0])
        request.extensions = {**request.extensions, "sni_hostname": url.host}
        if self._inner is not None:
            return await self._inner.handle_async_request(request)
        own = httpx.AsyncHTTPTransport()
        try:
            response = await own.handle_async_request(request)
        except BaseException:
            await own.aclose()
            raise
        response.stream = _Closing(response.stream, own)
        return response

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()


class _Closing(httpx.AsyncByteStream):
    """An answer's body that closes its own transport when it is closed."""

    def __init__(self, stream: httpx.SyncByteStream | httpx.AsyncByteStream, own) -> None:
        self._stream = stream
        self._own = own

    async def __aiter__(self):
        assert isinstance(self._stream, httpx.AsyncByteStream)
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            if isinstance(self._stream, httpx.AsyncByteStream):
                await self._stream.aclose()
        finally:
            await self._own.aclose()


def guarded(
    transport: httpx.AsyncBaseTransport | None, resolver: Resolver | None
) -> httpx.AsyncBaseTransport | None:
    """`transport` as a plugin sends through it: as it is for an address from the environment
    (`resolver` None), under `PublicOnlyTransport` for one set on the dashboard."""
    if resolver is None:
        return transport
    return PublicOnlyTransport(transport, resolver)
