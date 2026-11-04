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
import os
import shlex
import shutil
import socket
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh
from pydantic import BaseModel, SecretStr

from bleep.hosts.config import HostConfig

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_S = 15
KEEPALIVE_COUNT_MAX = 3  # a silent drop is noticed after about 45 s
CONNECT_TIMEOUT_S = 15
CONNECT_GRACE_S = 5  # the outer bound on a connect sits this far above asyncssh's own
OUTPUT_CAP = 1_000_000  # bytes kept of each of stdout and stderr
READ_CHUNK = 65_536
CLOSE_WAIT_S = 2
FORWARD_DIR_PREFIX = "bleep-ssh-"
STALE_AFTER_S = 60  # younger forward dirs may belong to a session that is still opening

# Forward dirs of this process's open sessions; the sweep never touches them.
_LIVE_DIRS: set[Path] = set()

Connect = Callable[..., Awaitable[Any]]
Scan = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class DashboardKeys:
    """A dashboard host's private key and the known_hosts line of its confirmed host key, as the
    secret file holds them now; None when unset."""

    private_key: SecretStr | None
    known_hosts: SecretStr | None


KeysFor = Callable[[str], DashboardKeys]


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


def _text(data: bytearray, cut: bool) -> str:
    text = bytes(data[:OUTPUT_CAP]).decode("utf-8", errors="replace")
    return text + f"\n[output cut at {OUTPUT_CAP} bytes]" if cut else text


async def _drain(reader: Any, sink: bytearray) -> bool:
    """Read until EOF into `sink`; True as soon as it holds more than OUTPUT_CAP bytes."""
    while chunk := await reader.read(READ_CHUNK):
        sink += chunk
        if len(sink) > OUTPUT_CAP:
            return True
    return False


def _listening(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def sweep_stale_forward_dirs() -> None:
    """Remove forward dirs that a crashed Bleep left in the temp dir.

    Only this user's `bleep-ssh-*` dirs older than STALE_AFTER_S whose socket nobody listens
    on, and never one of this process's open sessions.
    """
    root = Path(tempfile.gettempdir())
    for directory in root.glob(f"{FORWARD_DIR_PREFIX}*"):
        try:
            info = directory.lstat()
        except OSError:
            continue
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or info.st_uid != os.getuid()
            or time.time() - info.st_mtime < STALE_AFTER_S
            or directory in _LIVE_DIRS
            or _listening(directory / "docker.sock")
        ):
            continue
        shutil.rmtree(directory, ignore_errors=True)
        log.info("removed stale ssh forward dir %s", directory)


class SshSession:
    """The asyncssh connection to one ssh host, its socket forward and its command runner.

    `close()` is a deliberate stop: it sets `stopped` before closing the connection, so an event
    stream that ends because of it is not mistaken for a drop. A lost connection leaves `stopped`
    False and `connected` False; `ensure_open()` then builds a new connection and forward.
    """

    def __init__(
        self, host: HostConfig, connect: Connect = asyncssh.connect, keys: KeysFor | None = None
    ) -> None:
        """`keys` reads a dashboard host's keys from the secret file at every connect."""
        if host.kind != "ssh":
            raise ValueError(f"host {host.id} is a {host.kind} host, not an ssh host")
        self.host = host
        self._connect = connect
        self._keys = keys
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
        """Connect and forward the Docker socket; a no-op while already connected.

        Also clears forward dirs a crashed run left behind.
        """
        await asyncio.to_thread(sweep_stale_forward_dirs)
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

        A raw command string is refused. stdout and stderr are read as bytes, at most
        OUTPUT_CAP each. On the timeout or the cap the remote process is terminated and its
        channel closed, so a hung command never holds one of the server's sessions. Raises
        `TimeoutError` after `timeout` seconds and `SshUnavailable` when the connection is gone.
        """
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise TypeError("run takes a non-empty list of strings, never a command string")
        conn = self._conn
        if conn is None or conn.is_closed():
            raise SshUnavailable(self.host.id, "not connected")
        try:
            process = await conn.create_process(shlex.join(argv), encoding=None)
        except (asyncssh.Error, OSError) as exc:
            raise SshUnavailable(self.host.id, exc) from exc
        out, err = bytearray(), bytearray()
        finished = False
        cut = False
        readers = [
            asyncio.ensure_future(_drain(process.stdout, out)),
            asyncio.ensure_future(_drain(process.stderr, err)),
        ]
        try:
            async with asyncio.timeout(timeout):
                process.stdin.write_eof()
                for reader in asyncio.as_completed(readers):
                    if await reader:
                        cut = True
                        break
                else:
                    await process.wait_closed()
                    finished = True
        except TimeoutError:
            raise TimeoutError(
                f"{argv[0]} on {self.host.id} took longer than {timeout} s"
            ) from None
        except (asyncssh.Error, OSError) as exc:
            raise SshUnavailable(self.host.id, exc) from exc
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            await self._end(process, terminate=not finished)
        return CommandResult(
            exit_code=None if cut else process.exit_status,
            stdout=_text(out, cut),
            stderr=_text(err, cut),
        )

    async def _end(self, process: Any, terminate: bool) -> None:
        if terminate:
            with contextlib.suppress(OSError, asyncssh.Error):  # the channel is already gone
                process.terminate()
        process.close()
        with contextlib.suppress(TimeoutError, OSError, asyncssh.Error):
            async with asyncio.timeout(CLOSE_WAIT_S):
                await process.wait_closed()

    async def _rebuild(self) -> None:
        await self._teardown()
        options = self._dashboard_auth() if self.host.source == "dashboard" else self._file_auth()
        try:
            # asyncssh's own connect_timeout does not cover every stage of a handshake with a host
            # that vanished without a FIN, so an outer bound makes sure open() always returns.
            async with asyncio.timeout(CONNECT_TIMEOUT_S + CONNECT_GRACE_S):
                conn = await self._connect(
                    # OpenSSH lowercases the name before the known_hosts lookup; asyncssh does not,
                    # so "MacBook-Pro-M1.local" would miss the "macbook-pro-m1.local" entry.
                    (self.host.address or "").lower(),
                    port=self.host.port,
                    username=self.host.user,
                    # [] rather than asyncssh's default (), which loads ~/.ssh/config: only the
                    # hosts file or the dashboard decides where and how Bleep connects. No agent.
                    config=[],
                    agent_path=None,
                    **options,
                    keepalive_interval=KEEPALIVE_INTERVAL_S,
                    keepalive_count_max=KEEPALIVE_COUNT_MAX,
                    connect_timeout=CONNECT_TIMEOUT_S,
                )
        except (asyncssh.Error, OSError, TimeoutError) as exc:
            raise SshUnavailable(self.host.id, exc) from exc
        directory = Path(tempfile.mkdtemp(prefix=FORWARD_DIR_PREFIX))
        directory.chmod(0o700)
        _LIVE_DIRS.add(directory)
        socket = directory / "docker.sock"
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                listener = await conn.forward_local_path(str(socket), self.host.docker_socket)
        except (asyncssh.Error, OSError, TimeoutError) as exc:
            conn.close()
            _LIVE_DIRS.discard(directory)
            shutil.rmtree(directory, ignore_errors=True)
            raise SshUnavailable(self.host.id, exc) from exc
        self._conn, self._listener, self._dir, self._socket = conn, listener, directory, socket

    def _file_auth(self) -> dict[str, Any]:
        options: dict[str, Any] = {"client_keys": [str(self.host.key_file)]}
        if self.host.known_hosts_file is not None:
            options["known_hosts"] = str(self.host.known_hosts_file)
        elif not (Path.home() / ".ssh" / "known_hosts").is_file():
            # Left out, known_hosts makes asyncssh check ~/.ssh/known_hosts; with no file at
            # all there is nothing to check the host key against, so refuse.
            raise SshUnavailable(
                self.host.id, "no known_hosts_file set and no ~/.ssh/known_hosts to check against"
            )
        return options

    def _dashboard_auth(self) -> dict[str, Any]:
        """The key and the one host key confirmed on the dashboard, both from the secret file.
        Never ~/.ssh/known_hosts: a dashboard host trusts only the key a person confirmed. The
        errors are Bleep's own words, since a parser's could quote what it read."""
        keys = self._keys(self.host.id) if self._keys is not None else None
        if keys is None or keys.private_key is None:
            raise SshUnavailable(self.host.id, "no SSH key is set; enter it on the dashboard")
        if keys.known_hosts is None:
            raise SshUnavailable(
                self.host.id, "no host key is confirmed; confirm its fingerprint on the dashboard"
            )
        try:
            key = asyncssh.import_private_key(keys.private_key.get_secret_value())
        except asyncssh.KeyImportError, ValueError:
            raise SshUnavailable(
                self.host.id, "the SSH key set on the dashboard cannot be read"
            ) from None
        try:
            known_hosts = asyncssh.import_known_hosts(keys.known_hosts.get_secret_value())
        except ValueError:
            raise SshUnavailable(
                self.host.id, "the host key confirmed on the dashboard is invalid"
            ) from None
        return {"client_keys": [key], "known_hosts": known_hosts}

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
            _LIVE_DIRS.discard(directory)
            shutil.rmtree(directory, ignore_errors=True)


async def fetch_host_key(address: str, port: int, scan: Scan = asyncssh.get_server_host_key) -> Any:
    """The host key the SSH server at `address`:`port` offers, without logging in: the first half
    of trust on first use, which a person confirms by its fingerprint before it is stored. Raises
    `SshUnavailable` when no server answers."""
    host = address.lower()
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_S + CONNECT_GRACE_S):
            key = await scan(host, port, config=[])
    except (asyncssh.Error, OSError, TimeoutError) as exc:
        raise SshUnavailable(host, exc) from exc
    if key is None:
        raise SshUnavailable(host, "the server offered no host key")
    return key


def known_hosts_line(address: str, port: int, key: Any) -> str:
    """The known_hosts line that trusts `key` for `address`:`port`, and nothing else."""
    host = address.lower()
    pattern = host if port == 22 else f"[{host}]:{port}"
    return f"{pattern} {key.export_public_key('openssh').decode().strip()}"
