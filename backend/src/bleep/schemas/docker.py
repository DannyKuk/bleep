"""Docker-side shapes shared by the watcher and the engine."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ContainerSummary(BaseModel):
    id: str
    name: str
    image: str
    state: str  # Docker's own word: running, exited, restarting, ...
    health: Literal["healthy", "unhealthy", "starting", "none"] = "none"
    labels: dict[str, str] = Field(default_factory=dict)
    restart_policy: str = ""
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PortBinding(BaseModel):
    container_port: str  # "8000/tcp"
    host_ip: str | None = None
    host_port: str | None = None  # None when the port is exposed but not published


class MountInfo(BaseModel):
    type: str
    source: str
    destination: str
    read_only: bool


class ContainerDetail(BaseModel):
    """One container's full inspect, as Bleep uses it.

    `env` holds the raw `KEY=value` strings straight from the engine, secrets included. It must
    never reach a model, a log or the database without `guard.redact_env` first.
    """

    summary: ContainerSummary
    env: list[str] = Field(default_factory=list)
    ports: list[PortBinding] = Field(default_factory=list)
    mounts: list[MountInfo] = Field(default_factory=list)
    mem_limit: int | None = None  # bytes; None when the container has no limit
    oom_killed: bool = False
    restart_count: int = 0
    error: str | None = None


class ContainerStats(BaseModel):
    cpu_percent: float = 0.0  # 100 = one full CPU, as `docker stats` shows it
    mem_usage: int = 0  # bytes, page cache excluded
    mem_limit: int = 0
    mem_percent: float = 0.0
    net_rx: int = 0
    net_tx: int = 0
    block_read: int = 0
    block_write: int = 0
    pids: int = 0


class DockerEvent(BaseModel):
    time: datetime
    type: str  # container, image, network, ...
    action: str  # die, start, "health_status: unhealthy", ...
    container_id: str | None = None
    container_name: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class EngineInfo(BaseModel):
    name: str = ""
    server_version: str = ""
    operating_system: str = ""
    os_type: str = ""
    kernel_version: str = ""
    architecture: str = ""
    cpus: int = 0
    mem_total: int = 0  # bytes
    containers: int = 0
    containers_running: int = 0


class HostBasics(BaseModel):
    """Disk, memory and load of one host. Task 7 adds the fields; the registry only carries it."""
