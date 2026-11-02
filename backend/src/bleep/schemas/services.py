"""The services a human thinks in, and the state the registry keeps per host."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from bleep.hosts.config import HostConfig, UrlCheckConfig
from bleep.schemas.docker import ContainerSummary, HostBasics


class Service(BaseModel):
    id: str  # f"{host_id}/{key}"
    host_id: str
    name: str
    source: Literal["coolify", "compose", "single"]
    containers: list[ContainerSummary]
    urls: list[UrlCheckConfig] = Field(default_factory=list)
    repo: str | None = None
    docs_paths: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    protected: set[str] = Field(default_factory=set)  # container names act tools must refuse


@dataclass(frozen=True)
class HostState:
    config: HostConfig
    reachable: bool
    last_seen: datetime | None
    error: str | None = None
    basics: HostBasics | None = None
