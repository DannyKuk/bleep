"""The hosts file: which Docker hosts Bleep watches and how it reaches them.

Secrets never appear in the file. A secret is a reference, {env: NAME} or {file: PATH}.
"""

import os
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator


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


class ServiceOverrides(Strict):
    urls: list[UrlCheckConfig] = []
    repo: str | None = None
    docs_paths: list[str] = []
    protected_containers: list[str] = []
    rules: list[str] = []


class HostConfig(Strict):
    id: str
    name: str
    kind: Literal["ssh", "socket"]
    address: str | None = None
    user: str | None = None
    port: int = 22
    key_file: Path | None = None
    known_hosts_file: Path | None = None
    docker_socket: str = "/var/run/docker.sock"
    endpoint: str | None = None
    rules: list[str] = []
    services: dict[str, ServiceOverrides] = {}

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.kind == "ssh":
            missing = [f for f in ("address", "user", "key_file") if getattr(self, f) is None]
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
        return HostsFile.model_validate(data)
    except ValidationError as error:
        raise HostsFileError(f"invalid hosts file {path}:\n{error}") from error


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
