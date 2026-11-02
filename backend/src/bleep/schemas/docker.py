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
