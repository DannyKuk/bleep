"""The secrets and plugin addresses set from the dashboard, in one file apart from the database.

The file is JSON on the data volume, `secrets.json` next to `bleep.db`, mode 0600, and every write
replaces it whole through a temporary file created 0600 and renamed over it, so it is never
readable by anyone but the backend's user, not even halfway through a write. Values never leave
this module except through `get` (and `environ`, the same values as a mapping): states, errors,
logs and listeners carry names only. The environment wins over the file, and an address and the
token sent to it come whole from one place. See docs/design/settings.md.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

log = logging.getLogger(__name__)

FILE_MODE = 0o600
MAX_VALUE_CHARS = 16_384
FORMAT_VERSION = 1
FAMILY_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

# `multiline`: a secret that keeps its line breaks, such as an SSH private key.
Kind = Literal["secret", "address", "id", "multiline"]
Source = Literal["env", "dashboard"]
Listener = Callable[[frozenset[str]], Awaitable[None]]


class SecretFileError(Exception):
    """The secret file cannot be read; the text names the file, never what is in it."""


class UnknownSecret(KeyError):
    """No secret by that name can be set from the dashboard."""


class SecretRefused(ValueError):
    """The value cannot be stored; the text says why and never quotes it."""


class SecretFromEnv(SecretRefused):
    """The environment decides this one, so the dashboard can neither set nor clear it."""


class AddressStale(SecretRefused):
    """A token was sent without the version of its address, or with one that is no longer the
    address in force: it was typed for another address, so it is not stored. `missing` says
    which."""

    def __init__(self, message: str, *, missing: bool) -> None:
        super().__init__(message)
        self.missing = missing


class SecretNotSaved(Exception):
    """The change could not be made safe before writing (a listener that must run first
    failed), so nothing was written."""


@dataclass(frozen=True)
class SecretName:
    """One value the dashboard may set: a `secret`, an `address` a token is sent to (`carries`
    names that token), or an `id` that is not secret but lives with the secrets (a chat id).
    `required` says why it cannot be cleared from the dashboard, when something in force needs
    it (the tracer `TRACING` names). `private`: an address that may be on a private network or
    a Docker name when set from the dashboard, a model server's (#372)."""

    name: str
    kind: Kind = "secret"
    carries: str | None = None
    required: str | None = None
    private: bool = False


@dataclass(frozen=True)
class SecretFamily:
    """Values named `prefix` + an id, one per thing the dashboard adds (an SSH host's key).
    They are never read from the environment, never in `names()` or `environ()`, and are set and
    cleared only together through `replace`."""

    prefix: str
    kind: Kind = "secret"

    def owns(self, name: str) -> bool:
        return name.startswith(self.prefix) and bool(FAMILY_ID.fullmatch(name[len(self.prefix) :]))


@dataclass(frozen=True)
class SecretState:
    """Whether a value is set and where it comes from; `env_owned` when the environment decides
    it, set or not. Never the value."""

    set: bool
    source: Source | None
    env_owned: bool


class SecretStore:
    """`names` are the values the dashboard may set; `env` is read first (the process
    environment by default). The file is read once, here; `set` and `clear` write it."""

    def __init__(
        self,
        path: Path,
        names: Iterable[SecretName],
        *,
        families: Iterable[SecretFamily] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path
        self._names = {entry.name: entry for entry in names}
        self._families = tuple(families)
        self._env = env if env is not None else os.environ
        # An address and the token it carries form one group; every other name is its own.
        self._group: dict[str, frozenset[str]] = {}
        for entry in self._names.values():
            if entry.carries is not None:
                pair = frozenset({entry.name, entry.carries})
                self._group[entry.name] = self._group[entry.carries] = pair
        self._values: dict[str, SecretStr] = self._load()
        self._listeners: list[Listener] = []
        self._before: list[Listener] = []
        self._lock = asyncio.Lock()
        # Keys the address versions, so a version says nothing about the address it stands for
        # (a path can hold a credential); a new one each start makes old pages re-read.
        self._version_key = os.urandom(32)

    def __repr__(self) -> str:
        return f"SecretStore({self.path}, {len(self._values)} value(s) set)"

    def names(self) -> list[SecretName]:
        return list(self._names.values())

    def entry(self, name: str) -> SecretName:
        if name in self._names:
            return self._names[name]
        family = self._family(name)
        if family is None:
            raise UnknownSecret(name)
        return SecretName(name, kind=family.kind)

    def stored(self, family: SecretFamily) -> list[str]:
        """The names of `family` the file holds."""
        return sorted(name for name in self._values if family.owns(name))

    def get(self, name: str) -> SecretStr | None:
        """The value in force: the environment's when it decides `name`, else the file's."""
        if self._env_owned(name):
            value = self._env.get(name)
            return SecretStr(value) if value else None
        return self._values.get(name)

    def state(self, name: str) -> SecretState:
        self.entry(name)
        owned = self._env_owned(name)
        is_set = self.get(name) is not None
        source: Source | None = ("env" if owned else "dashboard") if is_set else None
        return SecretState(set=is_set, source=source, env_owned=owned)

    def environ(self) -> Mapping[str, str]:
        """The values in force as a live mapping, for code that reads the environment: a known
        name as `get` has it, any other from the environment."""
        return _Environ(self)

    def subscribe(self, listener: Listener, *, before_write: bool = False) -> None:
        """`listener` is awaited with the names that changed after each write that changed any.
        With `before_write` it is awaited before the file is written instead, and when it fails
        nothing is written (`SecretNotSaved`): for what must hold even if the write or the
        process stops halfway, such as a plugin switched off before its address changes."""
        (self._before if before_write else self._listeners).append(listener)

    def address_version(self, name: str) -> str | None:
        """An opaque version of the address `name` as the dashboard set it, or None when it is
        unset or the env decides it. A token is stored only with the version of the address it
        was typed for."""
        value = self._values.get(name) if not self._env_owned(name) else None
        if value is None or self.entry(name).kind != "address":
            return None
        digest = hmac.new(self._version_key, value.get_secret_value().encode(), hashlib.sha256)
        return digest.hexdigest()[:16]

    async def set(self, name: str, value: str, *, address_version: str | None = None) -> set[str]:
        """Store `value` for `name`; returns the names that changed. A new address clears the
        token it carries, and a token is stored only with `address_version`, the version of the
        address it was typed for, so a token is never sent to an address it was not entered for."""
        entry = self.entry(name)
        if self._family(name) is not None:
            raise UnknownSecret(name)  # set only with the rest of its family, through `replace`
        value = _checked(name, value, entry.kind)
        async with self._lock:
            self._refuse_env(name)
            for address, token in self._addresses():
                if token != name:
                    continue
                if address not in self._values:
                    raise SecretRefused(f"set {address} first: {name} is sent only there")
                if address_version is None:
                    raise AddressStale(
                        f"send the version of {address} the token was typed for", missing=True
                    )
                if not hmac.compare_digest(address_version, self.address_version(address) or ""):
                    raise AddressStale(
                        f"{address} changed since it was read; read it again and enter "
                        f"{name} for the address now set",
                        missing=False,
                    )
            current = self._values.get(name)
            if current is not None and current.get_secret_value() == value:
                return set()
            values = dict(self._values)
            values[name] = SecretStr(value)
            if entry.carries is not None:
                values.pop(entry.carries, None)
            return await self._commit(values)

    async def replace(self, changes: Mapping[str, str | None]) -> set[str]:
        """Set (a value) or clear (None) several names of a family in one write, so a key and the
        host key that goes with it are never stored apart; returns the names that changed. Every
        value is checked before anything is written."""
        checked: dict[str, SecretStr | None] = {}
        for name, value in changes.items():
            family = self._family(name)
            if family is None:
                raise UnknownSecret(name)
            checked[name] = None if value is None else SecretStr(_checked(name, value, family.kind))
        async with self._lock:
            values = dict(self._values)
            for name, value in checked.items():
                if value is None:
                    values.pop(name, None)
                else:
                    values[name] = value
            return await self._commit(values)

    async def clear(self, name: str) -> set[str]:
        """Remove `name` (and the token an address carries); returns the names that changed."""
        entry = self.entry(name)
        if self._family(name) is not None:
            raise UnknownSecret(name)
        async with self._lock:
            self._refuse_env(name)
            if entry.required is not None and name in self._values:
                raise SecretRefused(f"{name} cannot be cleared: {entry.required}")
            values = dict(self._values)
            values.pop(name, None)
            if entry.carries is not None:
                values.pop(entry.carries, None)
            return await self._commit(values)

    def _family(self, name: str) -> SecretFamily | None:
        return next((family for family in self._families if family.owns(name)), None)

    def _env_owned(self, name: str) -> bool:
        if self._family(name) is not None:
            return False
        return any(self._env.get(n) for n in self._group.get(name, frozenset({name})))

    def _refuse_env(self, name: str) -> None:
        if self._env_owned(name):
            together = " and ".join(sorted(self._group.get(name, {name})))
            raise SecretFromEnv(
                f"{together} come from the environment, which wins; change them there"
                if name in self._group
                else f"{name} is set in the environment, which wins; change it there"
            )

    def _addresses(self) -> Iterator[tuple[str, str]]:
        for entry in self._names.values():
            if entry.carries is not None:
                yield entry.name, entry.carries

    async def _commit(self, values: dict[str, SecretStr]) -> set[str]:
        changed = {
            name
            for name in set(values) | set(self._values)
            if (values.get(name) or SecretStr("")).get_secret_value()
            != (self._values.get(name) or SecretStr("")).get_secret_value()
        }
        if not changed:
            return set()
        for listener in self._before:
            try:
                await listener(frozenset(changed))
            except Exception as error:  # the type only: a message could quote a value
                log.warning("secrets: not saved, preparing failed (%s)", type(error).__name__)
                raise SecretNotSaved(
                    "the change could not be prepared; nothing was saved"
                ) from None
        await asyncio.to_thread(self._write, values)
        self._values = values
        for name in sorted(changed):
            log.info(
                "secrets: %s %s from the dashboard", name, "set" if name in values else "cleared"
            )
        await self._tell(frozenset(changed))
        return changed

    async def _tell(self, changed: frozenset[str]) -> None:
        for listener in self._listeners:
            try:
                await listener(changed)
            except Exception as error:  # the type only: a message could quote a value
                log.warning("secrets: reloading after a change failed (%s)", type(error).__name__)

    def _write(self, values: dict[str, SecretStr]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = {
            "version": FORMAT_VERSION,
            "values": {name: value.get_secret_value() for name, value in sorted(values.items())},
        }
        # mkstemp creates the file 0600 whatever the umask; fchmod says so explicitly.
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".secrets-", suffix=".tmp")
        try:
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(content, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        _sync_directory(self.path.parent)  # so the rename itself survives a power loss

    def _load(self) -> dict[str, SecretStr]:
        self._sweep()
        try:
            # O_NOFOLLOW: a link planted at the path is refused, never read or chmodded through.
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {}
        except OSError:
            raise SecretFileError(
                f"the secret file {self.path} cannot be read (a link, or no permission); "
                "fix or delete it"
            ) from None
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as file:
                mode = os.fstat(file.fileno()).st_mode
                if not stat.S_ISREG(mode):
                    raise SecretFileError(f"the secret file {self.path} is not a regular file")
                if mode & 0o077:
                    log.warning("secrets: %s was readable by others; made it 0600", self.path)
                    os.fchmod(file.fileno(), FILE_MODE)
                raw = json.loads(file.read())
        except OSError, ValueError:
            raise SecretFileError(
                f"the secret file {self.path} cannot be read; fix or delete it"
            ) from None
        values = raw.get("values") if isinstance(raw, dict) else None
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise SecretFileError(f"the secret file {self.path} is not in Bleep's format")
        known = {name for name in values if name in self._names or self._family(name) is not None}
        for name in sorted(set(values) - known):
            log.warning("secrets: %s in the secret file is not one Bleep knows; left out", name)
        return {name: SecretStr(v) for name, v in values.items() if name in known and v}

    def _sweep(self) -> None:
        """Delete temporary files a write left when the process was killed before its rename:
        they hold secrets too."""
        if not self.path.parent.is_dir():
            return
        for leftover in self.path.parent.glob(".secrets-*.tmp"):
            with contextlib.suppress(OSError):
                if leftover.is_file() and not leftover.is_symlink():
                    leftover.unlink()
                    log.warning("secrets: removed %s, left by an interrupted write", leftover.name)


def _sync_directory(directory: Path) -> None:
    with contextlib.suppress(OSError):  # not every file system lets a directory be opened
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _checked(name: str, value: str, kind: Kind = "secret") -> str:
    """`value` trimmed, or `SecretRefused`. A `multiline` value keeps its line feeds (a pasted
    CRLF becomes LF) and nothing else that is a control; every other kind keeps none."""
    breaks = {"\n"} if kind == "multiline" else set()
    if kind == "multiline":
        value = value.replace("\r\n", "\n")
    value = value.strip()
    if not value:
        raise SecretRefused(f"{name} is empty; clear it instead")
    if len(value) > MAX_VALUE_CHARS:
        raise SecretRefused(f"{name} is over {MAX_VALUE_CHARS:,} characters")
    # Cc: C0, DEL and C1 controls; Cf: invisible format characters; Zl and Zp: U+2028, U+2029.
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} and char not in breaks
        for char in value
    ):
        what = "a control" if breaks else "a line break, a control"
        raise SecretRefused(f"{name} holds {what} or an invisible character")
    return value


class _Environ(Mapping[str, str]):
    def __init__(self, store: SecretStore) -> None:
        self._store = store

    def __getitem__(self, name: str) -> str:
        if name in self._store._names:
            value = self._store.get(name)
            if value is None:
                raise KeyError(name)
            return value.get_secret_value()
        return self._store._env[name]

    def __iter__(self) -> Iterator[str]:
        return iter({*self._store._env, *(n for n in self._store._names if self._store.get(n))})

    def __len__(self) -> int:
        return sum(1 for _ in self)
