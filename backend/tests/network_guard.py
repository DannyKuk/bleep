"""Tests never touch the network: a connection or name lookup for a host that is not this machine
fails where it is made, and the test that was running (or the one before it, for a background
thread that outlived its test) fails at its end, because a thread swallows the error (#602).

The patches are installed once for the process and stay, so a thread a test left behind is
refused too. Loopback and unix sockets keep working.
"""

import ipaddress
import socket

_violations: list[str] = []


class NetworkUsed(OSError):
    """A test tried to reach a host outside this machine."""


def _is_local(host: object) -> bool:
    if host is None or host in ("", "localhost", "localhost.localdomain"):
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    try:
        address = ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _refuse(what: str, host: object) -> NetworkUsed:
    _violations.append(f"{what} {host!r}")
    return NetworkUsed(f"tests never touch the network: {what} {host!r}")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _address_host(sock: socket.socket, address: object) -> object:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
        return address[0]
    return None


def _connect(self: socket.socket, address: object) -> None:
    host = _address_host(self, address)
    if not _is_local(host):
        raise _refuse("connect to", host)
    return _real_connect(self, address)  # type: ignore[arg-type]


def _connect_ex(self: socket.socket, address: object) -> int:
    host = _address_host(self, address)
    if not _is_local(host):
        raise _refuse("connect to", host)
    return _real_connect_ex(self, address)  # type: ignore[arg-type]


def _getaddrinfo(host: object, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    # an IP literal, or a call that only parses one (asyncssh's), needs no lookup
    positional_flags = args[4] if len(args) > 4 else 0
    flags = int(kwargs.get("flags", positional_flags))  # type: ignore[call-overload]
    if flags & socket.AI_NUMERICHOST:
        return _real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]
    try:
        ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        if not _is_local(host):
            raise _refuse("name lookup of", host) from None
    return _real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]


socket.socket.connect = _connect  # type: ignore[method-assign,assignment]
socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign,assignment]
socket.getaddrinfo = _getaddrinfo  # type: ignore[assignment]


def take_violations() -> list[str]:
    """The attempts since the last call, cleared."""
    taken = list(_violations)
    _violations.clear()
    return taken
