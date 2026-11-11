"""The hosts file: which Docker hosts Bleep watches and how it reaches them.

Secrets never appear in the file. A secret is a reference, {env: NAME} or {file: PATH}.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Literal, Self

import httpx
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from bleep.hosts.coolify_names import coolify_slug

# A GitHub repo, owner/name; `.` and `..` are no names, since they would change a request's path.
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?$)[A-Za-z0-9._-]{1,100}")
MAX_DOCS_PATH = 300
# An `exits` entry: a container name or a Compose service name, by Docker's own rule for names.
EXIT_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
EVERY_CONTAINER = "*"
MAX_EXITS = 100
log = logging.getLogger(__name__)


class HostsFileError(Exception):
    """The hosts file is missing, is not valid YAML, or does not match the schema."""


class SecretMissing(Exception):
    """A secret reference points at an unset environment variable or a missing file."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretRef(Strict):
    env: str | None = None
    file: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_literal(cls, value: Any) -> Any:
        if not isinstance(value, dict | SecretRef):
            raise ValueError(
                "a secret must be a reference, {env: NAME} or {file: PATH}, never a literal value"
            )
        return value

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.env is None) == (self.file is None):
            raise ValueError("a secret reference needs exactly one of env or file")
        return self


class GithubConfig(Strict):
    token: SecretRef


class UrlCheckConfig(Strict):
    url: str
    expect_status: int = 200
    body_contains: str | None = None


def check_url_syntax(url: str) -> str | None:
    """Why `url` cannot be checked, or None when it can: an http(s) URL with a host."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as problem:
        return str(problem) or "not a URL"
    if parsed.scheme not in ("http", "https") or not parsed.host:
        return "must be an http:// or https:// URL with a host"
    return None


def normalise_docs_path(path: str) -> str:
    """`path` as the repo-relative path GitHub is asked for: `./docs`, `docs/` and `docs//ops`
    become `docs` and `docs/ops`. Raises ValueError for a path that leaves the repo or names
    nothing: absolute, with a `..` part, empty, or with a backslash or control character."""
    if path.strip().startswith("/"):
        raise ValueError(f"docs path {path!r} must be relative to the repo, such as docs")
    parts = [part for part in path.strip().split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"docs path {path!r} must stay inside the repo: no .. part")
    if not parts:
        raise ValueError(f"docs path {path!r} names no folder or file, such as docs")
    normal = "/".join(parts)
    if len(normal) > MAX_DOCS_PATH or re.search(r"[\\\x00-\x1f\x7f]", normal):
        raise ValueError(f"docs path {path[:80]!r} is not a file path inside the repo")
    return normal


def is_exit_name(entry: str) -> bool:
    """Whether `entry` can name a container or a Compose service (never `*`)."""
    return EXIT_NAME.fullmatch(entry) is not None


def check_exits(entries: list[str]) -> list[str]:
    """`exits` as stored: names by Docker's rule, `*` only alone, at most `MAX_EXITS`. The
    errors never quote an entry: one may come from a container's label."""
    if EVERY_CONTAINER in entries and len(set(entries)) > 1:
        raise PydanticCustomError(
            "exits_star_alone",
            'Value error, "*" stands alone in exits: it already names every container',
        )
    if any(e != EVERY_CONTAINER and not is_exit_name(e) for e in entries):
        raise PydanticCustomError(
            "exits_names",
            "Value error, exits entries are container or Compose service names: letters, "
            "digits, _ . -, starting with a letter or digit, at most 128 characters",
        )
    if len(set(entries)) > MAX_EXITS:
        raise PydanticCustomError(
            "exits_too_many",
            "Value error, exits holds at most {limit} entries",
            {"limit": MAX_EXITS},
        )
    return list(dict.fromkeys(entries))


class ServiceOverrides(Strict):
    urls: list[UrlCheckConfig] = []
    repo: str | None = None
    docs_paths: list[str] = []
    readme: bool = True
    protected_containers: list[str] = []
    # Jobs meant to finish: a container name, a Compose service name or "*" (design/watcher.md).
    exits: list[str] = []
    rules: list[str] = []

    @field_validator("repo")
    @classmethod
    def _repo_name(cls, value: str | None) -> str | None:
        if value is not None and not REPO_NAME.fullmatch(value):
            raise ValueError(f"repo {value!r} must be a GitHub repo name, owner/name")
        return value

    @field_validator("exits")
    @classmethod
    def _exits(cls, value: list[str]) -> list[str]:
        return check_exits(value)

    @field_validator("docs_paths")
    @classmethod
    def _docs_paths(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(normalise_docs_path(path) for path in value))


class HostConfig(Strict):
    id: str
    name: str
    kind: Literal["ssh", "socket"]
    # A dashboard host's key and host key live in the secret file, never in a field here.
    source: Literal["file", "dashboard"] = "file"
    address: str | None = None
    user: str | None = None
    port: int = 22
    key_file: Path | None = None
    known_hosts_file: Path | None = None
    docker_socket: str = "/var/run/docker.sock"
    endpoint: str | None = None
    # A machine that sleeps by design (a laptop): not answering opens no incident.
    may_sleep: bool = False
    rules: list[str] = []
    services: dict[str, ServiceOverrides] = {}

    @model_validator(mode="after")
    def _urls_are_checkable(self) -> Self:
        for name, service in self.services.items():
            for cfg in service.urls:
                if (why := check_url_syntax(cfg.url)) is not None:
                    raise ValueError(
                        f"host {self.id!r}, service {name!r}: invalid url {cfg.url!r}: {why}"
                    )
        return self

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.source == "dashboard":
            if self.kind != "ssh":
                raise ValueError("a host added on the dashboard is an ssh host")
            if self.key_file is not None or self.known_hosts_file is not None:
                raise ValueError("a dashboard host's keys are in the secret file, not in files")
        if self.kind == "ssh":
            needed = (
                ("address", "user", "key_file") if self.source == "file" else ("address", "user")
            )
            missing = [f for f in needed if getattr(self, f) is None]
            if missing:
                raise ValueError(f"an ssh host needs {', '.join(missing)}")
            if self.endpoint is not None:
                raise ValueError("endpoint is only for socket hosts")
        else:
            if self.endpoint is None:
                raise ValueError("a socket host needs endpoint")
            if not self.endpoint.startswith(("unix://", "tcp://")):
                raise ValueError("endpoint must start with unix:// or tcp://")
            extra = [
                f
                for f in ("address", "user", "key_file", "known_hosts_file")
                if getattr(self, f) is not None
            ]
            if extra:
                raise ValueError(f"a socket host cannot set {', '.join(extra)}; those are ssh only")
        return self


class HostsFile(Strict):
    hosts: list[HostConfig]
    github: GithubConfig | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        seen: set[str] = set()
        for host in self.hosts:
            if host.source != "file":
                raise ValueError(
                    f"host {host.id!r}: source is Bleep's to set, not the hosts file's"
                )
            if host.id in seen:
                raise ValueError(f"duplicate host id {host.id!r}")
            seen.add(host.id)
        return self


def load_hosts_file(path: Path) -> HostsFile:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise HostsFileError(f"cannot read hosts file {path}: {error}") from error
    try:
        hosts_file = HostsFile.model_validate(data)
    except ValidationError as error:
        raise HostsFileError(f"invalid hosts file {path}:\n{error}") from error
    _warn_empty_slugs(hosts_file)
    return hosts_file


def _warn_empty_slugs(hosts_file: HostsFile) -> None:
    for host in hosts_file.hosts:
        for overrides in host.services.values():
            for name in overrides.protected_containers:
                if not coolify_slug(name):
                    log.warning(
                        "host %s: protected entry %r has no letters or digits for Coolify's slug,"
                        " so it protects nothing by a Coolify name; protect the application by"
                        " its uuid",
                        host.id,
                        name,
                    )


def resolve_secret(ref: SecretRef) -> SecretStr:
    if ref.env is not None:
        value = os.environ.get(ref.env)
        if value is None:
            raise SecretMissing(f"environment variable {ref.env} is not set")
        return SecretStr(value)
    assert ref.file is not None
    try:
        return SecretStr(ref.file.read_text(encoding="utf-8").strip())
    except OSError as error:
        raise SecretMissing(f"secret file {ref.file} cannot be read: {error}") from error
