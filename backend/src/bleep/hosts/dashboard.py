"""The hosts added on the dashboard: how Bleep reaches each one in SQLite, its SSH key and the
host key a person confirmed in the secret file (`store/secrets.py`), never in the database.

Three rules hold here (docs/design/hosts.md):
- Trust on first use is explicit: Bleep fetches the host key an address offers and shows its
  fingerprint, and stores it only when a person sends that fingerprint back with the host.
- A key is bound to its connection: a new address, port or user clears the key and the host key
  in the same write, unless new ones come with it, and every change names the `version` of the
  host it was built from, so a key typed for one connection is never stored with another.
- The hosts file wins: a host it names is never added, changed or removed here.

Every change is told to the listeners (the watcher manager, the settings store), which put it in
force without a restart.
"""

import asyncio
import hashlib
import hmac
import logging
import secrets as random
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncssh
from pydantic import BaseModel, SecretStr

from bleep.hosts.config import HostConfig
from bleep.hosts.ssh import (
    FETCH_TIMEOUT_S,
    AddressRefused,
    DashboardKeys,
    KeysFor,
    Resolve,
    Scan,
    SshUnavailable,
    fetch_host_key,
    known_hosts_line,
    vetted_address,
)
from bleep.schemas.dashboard_hosts import (
    DashboardHostEdit,
    HostKeyConfirmation,
    HostKeyOffer,
    NewDashboardHost,
)
from bleep.schemas.refusal import Templated
from bleep.schemas.settings import HostConnectionView, SecretStatus
from bleep.store.db import Database
from bleep.store.secrets import SecretFamily, SecretStore

log = logging.getLogger(__name__)

SSH_KEYS = SecretFamily("SSH_KEY:", kind="multiline")
HOST_KEYS = SecretFamily("KNOWN_HOSTS:")
OFFER_TTL = timedelta(minutes=10)
MAX_OFFERS = 16  # host keys fetched and not yet confirmed; the oldest goes first

ChangeKind = Literal["added", "connection", "renamed", "removed"]


class HostRefused(Templated, ValueError):
    """The host cannot be saved as sent (422); the text never quotes a key."""


class HostConflict(Templated, Exception):
    """The host cannot be saved now (409): the hosts file names it, it exists already, or the
    host key confirmed is not the one offered."""


class HostKeyUnavailable(Templated, Exception):
    """No host key could be fetched (502). Every cause gets this one sentence, so the answer does
    not tell a closed port from an open one or a silent one; the cause is logged by type."""

    def __init__(self) -> None:
        super().__init__("Could not get an SSH host key from that address.", "host_key_unavailable")


class HostKeyBusy(Templated, Exception):
    """Another host key is being fetched (429): one at a time."""

    def __init__(self) -> None:
        super().__init__(
            "another host key is being fetched; try again in a few seconds", "host_key_busy"
        )


class HostStale(Exception):
    """The host changed since the version the change was built from (409)."""

    def __init__(self, current: str) -> None:
        super().__init__("the host changed since this version was read")
        self.current = current


class UnknownHost(KeyError):
    """No host by that id was added on the dashboard (404)."""


@dataclass(frozen=True)
class HostChange:
    """What changed: `config` is the host as it is now, None once removed. `connection` means the
    address, port, user or Docker socket changed, so Bleep connects again."""

    host_id: str
    kind: ChangeKind
    config: HostConfig | None
    moved: bool = False  # a new address, port or user: possibly another machine


Listener = Callable[[HostChange], Awaitable[None]]


class _Row(BaseModel):
    id: str
    name: str
    address: str
    port: int
    user: str
    docker_socket: str
    revision: int = 0

    def config(self) -> HostConfig:
        return HostConfig(
            id=self.id,
            name=self.name,
            kind="ssh",
            source="dashboard",
            address=self.address,
            port=self.port,
            user=self.user,
            docker_socket=self.docker_socket,
        )

    def login(self) -> tuple[str, int, str]:
        return self.address, self.port, self.user


@dataclass(frozen=True)
class _Offer:
    address: str
    port: int
    line: str
    fingerprint: str
    expires: datetime


def dashboard_keys(store: SecretStore) -> KeysFor:
    """A host's key and host key as the secret file holds them now, read at every connect."""

    def read(host_id: str) -> DashboardKeys:
        return DashboardKeys(
            store.get(SSH_KEYS.prefix + host_id), store.get(HOST_KEYS.prefix + host_id)
        )

    return read


class DashboardHosts:
    """`file_ids` are the hosts file's hosts, which win. `scan` fetches a host key (asyncssh's
    `get_server_host_key`) and `resolve` looks up an address; tests pass fakes of both."""

    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        file_ids: Iterable[str],
        scan: Scan = asyncssh.get_server_host_key,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        resolve: Resolve | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._file_ids = frozenset(file_ids)
        self.scan = scan
        self.resolve = resolve
        self._clock = clock
        self._fetching = asyncio.Lock()
        self._rows: dict[str, _Row] = {}
        self._offers: dict[str, _Offer] = {}
        self._listeners: list[Listener] = []
        self._lock = asyncio.Lock()

    def subscribe(self, listener: Listener) -> None:
        """`listener` is awaited after each change, in the order of subscribing."""
        self._listeners.append(listener)

    async def load(self) -> list[HostConfig]:
        """Read the stored hosts; the ones in force, in the order they were added. A host the
        hosts file names now is left out with a warning (the file wins), and a key in the secret
        file for a host that no longer exists is removed."""
        conn = await self._db.connect()
        try:
            async with conn.execute(
                "SELECT id, name, address, port, user, docker_socket, revision"
                " FROM dashboard_hosts ORDER BY created_at, id"
            ) as cursor:
                found = await cursor.fetchall()
        finally:
            await conn.close()
        self._rows = {row["id"]: _Row(**dict(row)) for row in found}
        orphans = {
            name: None
            for family in (SSH_KEYS, HOST_KEYS)
            for name in self._secrets.stored(family)
            if name.removeprefix(family.prefix) not in self._rows
        }
        if orphans:
            log.warning("hosts: removed the keys of %d host(s) no longer added", len(orphans))
            await self._secrets.replace(orphans)
        for host_id in sorted(self._rows.keys() & self._file_ids):
            log.warning(
                "hosts: %s is in the hosts file, which wins; its dashboard entry is unused", host_id
            )
        return self.configs()

    def configs(self) -> list[HostConfig]:
        return [row.config() for row in self._rows.values() if row.id not in self._file_ids]

    def version(self, host_id: str) -> str:
        row = self._row(host_id)
        return hashlib.sha256(row.model_dump_json().encode()).hexdigest()[:16]

    def connection(self, host_id: str) -> HostConnectionView:
        """The host's connection as the API shows it: its keys only as set or not."""
        row = self._row(host_id)
        return HostConnectionView(
            source="dashboard",
            kind="ssh",
            address=row.address,
            user=row.user,
            port=row.port,
            docker_socket=row.docker_socket,
            ssh_key=self._status(SSH_KEYS, host_id),
            host_key=self._status(HOST_KEYS, host_id),
            version=self.version(host_id),
        )

    async def offer_host_key(self, address: str, port: int) -> HostKeyOffer:
        """Fetch the host key the server at `address`:`port` offers and keep it for ten minutes,
        to be confirmed by its fingerprint. Nothing is stored. One fetch runs at a time
        (`HostKeyBusy`), and every failure is the same `HostKeyUnavailable`."""
        if self._fetching.locked():
            raise HostKeyBusy()
        async with self._fetching:
            try:
                key = await fetch_host_key(address, port, self.scan, self.resolve)
            except Exception as failure:  # whatever went wrong, the answer is the same sentence
                cause = failure.cause if isinstance(failure, SshUnavailable) else failure
                log.info("hosts: no host key fetched (%s)", type(cause).__name__)
                raise HostKeyUnavailable() from None
        now = self._clock()
        offer = _Offer(
            address=address.lower(),
            port=port,
            line=known_hosts_line(address, port, key),
            fingerprint=key.get_fingerprint("sha256"),
            expires=now + OFFER_TTL,
        )
        self._offers = {k: o for k, o in self._offers.items() if o.expires > now}
        while len(self._offers) >= MAX_OFFERS:
            del self._offers[next(iter(self._offers))]
        offer_id = random.token_urlsafe(16)
        self._offers[offer_id] = offer
        return HostKeyOffer(
            offer=offer_id,
            address=offer.address,
            port=port,
            fingerprint=offer.fingerprint,
            key_type=key.get_algorithm(),
        )

    async def add(self, new: NewDashboardHost) -> HostConfig:
        """Save a new host with its key and its confirmed host key, all or nothing."""
        async with self._lock:
            if new.id in self._file_ids:
                raise HostConflict(
                    f"the hosts file names host {new.id}; change it there",
                    "host_in_file",
                    host=new.id,
                )
            if new.id in self._rows:
                raise HostConflict(f"host {new.id} exists already", "host_exists", host=new.id)
            key = _private_key(new.ssh_key)
            line = self._confirmed(new.host_key, new.address, new.port)
            await self._vet(new.address, new.port)
            row = _Row(**new.model_dump(include=set(_Row.model_fields)))
            await self._write(row, created=True)
            try:
                await self._secrets.replace(_keys(row.id, key, line))
            except BaseException:
                await self._delete(row.id)
                raise
            self._offers.pop(new.host_key.offer, None)
            self._rows[row.id] = row
            log.info("hosts: %s added on the dashboard", row.id)
            await self._tell(HostChange(row.id, "added", row.config()))
            return row.config()

    async def change(self, host_id: str, edit: DashboardHostEdit, *, version: str) -> HostConfig:
        """Change a host built from `version`. A new address, port or user clears its key and host
        key unless new ones come with it; the keys are written first, so a crash before the row
        leaves the old connection with keys that its host key check refuses or none at all."""
        async with self._lock:
            old = self._editable(host_id)
            if not _same(version, self.version(host_id)):
                raise HostStale(self.version(host_id))
            row = _Row(
                **edit.model_dump(include=set(_Row.model_fields) - {"revision"}),
                id=host_id,
                revision=old.revision + 1,
            )
            moved = row.login() != old.login()
            keys: dict[str, str | None] = {}
            if moved:
                keys = {SSH_KEYS.prefix + host_id: None, HOST_KEYS.prefix + host_id: None}
            if edit.ssh_key is not None:
                keys[SSH_KEYS.prefix + host_id] = _private_key(edit.ssh_key)
            if edit.host_key is not None:
                line = self._confirmed(edit.host_key, row.address, row.port)
                keys[HOST_KEYS.prefix + host_id] = line
            if (row.address, row.port) != (old.address, old.port):
                await self._vet(row.address, row.port)
            if keys:
                await self._secrets.replace(keys)
            await self._write(row)
            if edit.host_key is not None:
                self._offers.pop(edit.host_key.offer, None)
            self._rows[host_id] = row
            if moved:
                log.info("hosts: %s connects elsewhere now; its old keys were cleared", host_id)
            kind: ChangeKind = (
                "connection"
                if moved or row.docker_socket != old.docker_socket or keys
                else "renamed"
            )
            await self._tell(HostChange(host_id, kind, row.config(), moved=moved))
            return row.config()

    async def remove(self, host_id: str, *, version: str) -> None:
        """Remove a host and its keys. One the hosts file shadows is removed from here only."""
        async with self._lock:
            if host_id in self._file_ids and host_id not in self._rows:
                raise HostConflict(
                    f"the hosts file names host {host_id}; change it there",
                    "host_in_file",
                    host=host_id,
                )
            self._row(host_id)
            if not _same(version, self.version(host_id)):
                raise HostStale(self.version(host_id))
            await self._secrets.replace(_keys(host_id, None, None))
            await self._delete(host_id)
            del self._rows[host_id]
            log.info("hosts: %s removed on the dashboard", host_id)
            if host_id not in self._file_ids:
                await self._tell(HostChange(host_id, "removed", None))

    def _row(self, host_id: str) -> _Row:
        if host_id not in self._rows:
            raise UnknownHost(host_id)
        return self._rows[host_id]

    def _editable(self, host_id: str) -> _Row:
        if host_id in self._file_ids:
            raise HostConflict(
                f"the hosts file names host {host_id}; change it there",
                "host_in_file",
                host=host_id,
            )
        return self._row(host_id)

    def _status(self, family: SecretFamily, host_id: str) -> SecretStatus:
        is_set = self._secrets.get(family.prefix + host_id) is not None
        return SecretStatus(set=is_set, source="dashboard" if is_set else None)

    def _confirmed(self, confirmation: HostKeyConfirmation, address: str, port: int) -> str:
        """The known_hosts line of the offer a person confirmed, for this address and port."""
        offer = self._offers.get(confirmation.offer)
        if offer is None or offer.expires <= self._clock():
            raise HostConflict(
                "the host key offer is unknown or expired; fetch the host key again",
                "host_offer_unknown",
            )
        if (offer.address, offer.port) != (address.lower(), port):
            raise HostRefused(
                "the host key was fetched for another address or port", "host_offer_other"
            )
        if not _same(offer.fingerprint, confirmation.fingerprint):
            raise HostConflict(
                "the fingerprint confirmed is not the one Bleep was offered",
                "host_fingerprint_differs",
            )
        return offer.line  # the offer is used up once the host is saved

    async def _vet(self, address: str, port: int) -> None:
        """Refuse an address a dashboard host may never be reached at, as the fetch did."""
        try:
            async with asyncio.timeout(FETCH_TIMEOUT_S):
                await vetted_address(address.lower(), port, self.resolve)
        except AddressRefused as refused:
            raise HostRefused(str(refused), refused.template, **refused.values) from None
        except TimeoutError:
            raise HostRefused("the address does not resolve", "address_unresolved") from None

    async def _tell(self, change: HostChange) -> None:
        for listener in self._listeners:
            try:
                await listener(change)
            except Exception:  # the change is saved; a restart puts it in force at the latest
                log.exception("hosts: putting the change of %s in force failed", change.host_id)

    async def _write(self, row: _Row, *, created: bool = False) -> None:
        now = self._clock().isoformat()
        values: dict[str, Any] = row.model_dump() | {"now": now}
        conn = await self._db.connect()
        try:
            if created:
                await conn.execute(
                    "INSERT INTO dashboard_hosts (id, name, address, port, user, docker_socket,"
                    " revision, created_at, updated_at) VALUES (:id, :name, :address, :port,"
                    " :user, :docker_socket, :revision, :now, :now)",
                    values,
                )
            else:
                await conn.execute(
                    "UPDATE dashboard_hosts SET name = :name, address = :address, port = :port,"
                    " user = :user, docker_socket = :docker_socket, revision = :revision,"
                    " updated_at = :now WHERE id = :id",
                    values,
                )
            await conn.commit()
        finally:
            await conn.close()

    async def _delete(self, host_id: str) -> None:
        conn = await self._db.connect()
        try:
            await conn.execute("DELETE FROM dashboard_hosts WHERE id = ?", (host_id,))
            await conn.commit()
        finally:
            await conn.close()


def _keys(host_id: str, key: str | None, line: str | None) -> dict[str, str | None]:
    return {SSH_KEYS.prefix + host_id: key, HOST_KEYS.prefix + host_id: line}


def _same(sent: str, current: str) -> bool:
    """A constant-time comparison that takes any text: `compare_digest` refuses non-ASCII str."""
    return hmac.compare_digest(sent.encode(), current.encode())


def _private_key(key: SecretStr) -> str:
    """The key in OpenSSH form, exported again by Bleep, so whatever was pasted around it is never
    stored; one key only. Refused in Bleep's words only, since a parser's message could quote
    what it read."""
    text = key.get_secret_value()
    if text.count("-----BEGIN") > 1:
        raise HostRefused("ssh_key must hold one private key, not several", "ssh_key_several")
    try:
        return asyncssh.import_private_key(text).export_private_key().decode()
    except asyncssh.KeyImportError, asyncssh.KeyExportError, ValueError:
        raise HostRefused(
            "ssh_key must be an unencrypted private key (OpenSSH or PEM); Bleep has no agent "
            "to ask for a passphrase",
            "ssh_key_unusable",
        ) from None
