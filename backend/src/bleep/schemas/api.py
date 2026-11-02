"""The HTTP shapes of the read API. `/openapi.json` carries them to the dashboard."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Health = Literal["ok", "degraded", "down", "stopped", "unknown"]


class Me(BaseModel):
    authenticated: bool


class DiskView(BaseModel):
    mount: str
    used_percent: float


class BasicsView(BaseModel):
    os: str
    # None when the host could not tell (a socket host has no load; a parse can fail)
    cpus: int | None
    mem_total: int | None
    mem_available: int | None
    load1: float | None
    load15: float | None
    disks: list[DiskView]


class HostView(BaseModel):
    id: str
    name: str
    kind: Literal["socket", "ssh"]
    reachable: bool
    last_seen: datetime | None
    error: str | None
    basics: BasicsView | None
    service_count: int
    health: Health


class ContainerView(BaseModel):
    id: str
    name: str
    image: str
    state: str
    health: Literal["healthy", "unhealthy", "starting", "none"]
    restart_count: int
    exit_code: int | None
    protected: bool


class UrlStatus(BaseModel):
    url: str
    status: Literal["unknown", "ok", "failing"]
    last_checked: datetime | None
    status_code: int | None
    latency_ms: int | None


class ServiceView(BaseModel):
    id: str
    host_id: str
    name: str
    source: Literal["coolify", "compose", "single"]
    health: Health
    containers: list[ContainerView]
    urls: list[UrlStatus]
    open_incident_id: str | None
