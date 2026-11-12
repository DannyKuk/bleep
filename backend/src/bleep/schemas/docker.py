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
    restart_count: int = 0
    exit_code: int | None = None
    started_at: datetime | None = None  # the last start: a restart moves it
    finished_at: datetime | None = None
    created_at: datetime | None = None  # when the container was made; a restart keeps it


class PortBinding(BaseModel):
    container_port: str  # "8000/tcp"
    host_ip: str | None = None
    host_port: str | None = None  # None when the port is exposed but not published


class MountInfo(BaseModel):
    type: str
    source: str
    destination: str
    read_only: bool


class HealthCheck(BaseModel):
    """A container's health check as configured. An unset number means Docker's default."""

    command: list[str]  # the argv of a `CMD` check, or the one string of a `CMD-SHELL` check
    shell: bool = False  # `CMD-SHELL`: a shell runs the string; otherwise argv runs as it is
    interval_s: float | None = None
    timeout_s: float | None = None
    retries: int | None = None
    start_period_s: float | None = None


class HealthProbe(BaseModel):
    """One recorded run of the health check. Docker records a timeout as exit code -1."""

    start: datetime | None = None
    end: datetime | None = None
    exit_code: int | None = None
    output: str = ""  # untrusted: whatever the probe printed


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
    healthcheck: HealthCheck | None = None  # None when the container has no health check
    probes: list[HealthProbe] = Field(default_factory=list)  # oldest first; Docker keeps 5


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
    system_time: datetime | None = None  # the engine host's clock when it answered


class LastLog(BaseModel):
    """When a container last wrote a log line, on the same clock as `now`: the engine host's."""

    at: datetime | None  # None: it has written nothing yet
    now: datetime


class DiskUsage(BaseModel):
    mount: str
    used_percent: float


class HostBasics(BaseModel):
    """Disk, memory and load of one host. A field that could not be read is None."""

    os: str = ""  # "Linux", "Darwin", or the engine's OS type for a socket host
    disks: list[DiskUsage] = Field(default_factory=list)
    mem_total: int | None = None  # bytes
    mem_available: int | None = None  # bytes
    load1: float | None = None
    load15: float | None = None
    cpus: int | None = None
