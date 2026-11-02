"""One SSH connection per host: it forwards the Docker socket and runs host commands.

See docs/decisions/docker-over-ssh.md. The remote Docker socket is forwarded to a local unix
socket in a private temp dir, so the docker SDK talks to the host without `docker` on its PATH,
and the same connection runs the allowlisted shell commands.

Reconnecting is not done here on its own: the watcher calls `ensure_open()` and then
`engine.reconnect()` (see docs/design/hosts.md).
"""

import asyncio
import contextlib
import logging
import shlex
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncssh
from pydantic import BaseModel

from bleep.hosts.config import HostConfig

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_S = 15
KEEPALIVE_COUNT_MAX = 3  # a silent drop is noticed after about 45 s
CONNECT_TIMEOUT_S = 15
OUTPUT_CAP = 1_000_000  # characters kept of each of stdout and stderr

Connect = Callable[..., Awaitable[Any]]


class SshUnavailable(Exception):
    """The SSH connection could not be opened, was lost, or the session was closed."""

    def __init__(self, host_id: str, cause: BaseException | str) -> None:
        super().__init__(f"{host_id}: ssh unavailable: {_describe(cause)}")
        self.host_id = host_id
        self.cause = cause


def _describe(cause: BaseException | str) -> str:
    """The cause as text; a bare TimeoutError has an empty message, so use its type then."""
    return str(cause) or type(cause).__name__


class CommandResult(BaseModel):
    exit_code: int | None  # None when the command was killed by a signal
    stdout: str
    stderr: str


def _cap(text: str | None) -> str:
    text = text or ""
    if len(text) <= OUTPUT_CAP:
        return text
    return text[:OUTPUT_CAP] + f"\n[output cut at {OUTPUT_CAP} characters]"


class SshSession:
    """The asyncssh connection to one ssh host, its socket forward and its command runner.

    `close()` is a deliberate stop: it sets `stopped` before closing the connection, so an event
    stream that ends because of it is not mistaken for a drop. A lost connection leaves `stopped`
    False and `connected` False; `ensure_open()` then builds a new connection and forward.
    """

    def __init__(self, host: HostConfig, connect: Connect = asyncssh.connect) -> None:
        if host.kind != "ssh":
            raise ValueError(f"host {host.id} is a {host.kind} host, not an ssh host")
        self.host = host
        self._connect = connect
        self._conn: Any = None
        self._listener: Any = None
        self._dir: Path | None = None
        self._socket: Path | None = None
        self._stopped = False
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    @property
    def stopped(self) -> bool:
        """True after `close()`, until the next `open()`."""
        return self._stopped

    @property
    def forwarded_socket(self) -> Path:
        """The local unix socket that leads to the host's Docker socket."""
        if not self.connected or self._socket is None:
            raise SshUnavailable(self.host.id, "not connected")
        return self._socket

    async def open(self) -> None:
        """Connect and forward the Docker socket; a no-op while already connected."""
        async with self._lock:
            self._stopped = False
            if not self.connected:
                await self._rebuild()

    async def ensure_open(self) -> None:
        """Rebuild the connection and the forward when they were lost; idempotent.

        Raises `SshUnavailable` when the host cannot be reached or the session was closed.
        """
        async with self._lock:
            if self._stopped:
                raise SshUnavailable(self.host.id, "session was closed")
            if not self.connected:
                await self._rebuild()

    async def close(self) -> None:
        self._stopped = True  # before closing, so the ending event stream reads as a stop
        async with self._lock:
            await self._teardown()

    async def run(self, argv: list[str], timeout: float = 20.0) -> CommandResult:
        """Run one command given as an argv list; every argument is quoted with `shlex.join`.

        A raw command string is refused. Raises `TimeoutError` after `timeout` seconds and
        `SshUnavailable` when the connection is gone.
        """
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise TypeError("run takes a non-empty list of strings, never a command string")
        conn = self._conn
        if conn is None or conn.is_closed():
            raise SshUnavailable(self.host.id, "not connected")
        try:
            async with asyncio.timeout(timeout):
                done = await conn.run(
                    shlex.join(argv), check=False, encoding="utf-8", errors="replace"
                )
        except TimeoutError:
            raise TimeoutError(
                f"{argv[0]} on {self.host.id} took longer than {timeout} s"
            ) from None
        except (asyncssh.Error, OSError) as exc:
            raise SshUnavailable(self.host.id, exc) from exc
        return CommandResult(
            exit_code=done.exit_status, stdout=_cap(done.stdout), stderr=_cap(done.stderr)
        )

    async def _rebuild(self) -> None:
        await self._teardown()
        known_hosts = str(self.host.known_hosts_file) if self.host.known_hosts_file else None
        if known_hosts is None:
            log.warning(
                "host %s has no known_hosts_file: its SSH host key is not checked", self.host.id
            )
        try:
            conn = await self._connect(
                # OpenSSH lowercases the name before the known_hosts lookup; asyncssh does not,
                # so "MacBook-Pro-M1.local" would miss the "macbook-pro-m1.local" entry.
                (self.host.address or "").lower(),
                port=self.host.port,
                username=self.host.user,
                client_keys=[str(self.host.key_file)],
                known_hosts=known_hosts,
                keepalive_interval=KEEPALIVE_INTERVAL_S,
                keepalive_count_max=KEEPALIVE_COUNT_MAX,
                connect_timeout=CONNECT_TIMEOUT_S,
            )
        except (asyncssh.Error, OSError) as exc:
            raise SshUnavailable(self.host.id, exc) from exc
        directory = Path(tempfile.mkdtemp(prefix="bleep-ssh-"))
        directory.chmod(0o700)
        socket = directory / "docker.sock"
        try:
            listener = await conn.forward_local_path(str(socket), self.host.docker_socket)
        except (asyncssh.Error, OSError) as exc:
            conn.close()
            shutil.rmtree(directory, ignore_errors=True)
            raise SshUnavailable(self.host.id, exc) from exc
        self._conn, self._listener, self._dir, self._socket = conn, listener, directory, socket

    async def _teardown(self) -> None:
        conn, listener, directory = self._conn, self._listener, self._dir
        self._conn = self._listener = self._dir = self._socket = None
        if listener is not None:
            listener.close()
        if conn is not None:
            conn.close()
            with contextlib.suppress(asyncssh.Error, OSError):  # already gone
                await conn.wait_closed()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
