from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

QuietMode = Literal["snooze", "leave_alone"]
QuietScope = Literal["service", "host"]


class QuietState(BaseModel):
    scope: QuietScope
    target_id: str
    mode: QuietMode
    until: datetime | None  # None: left alone until a person says otherwise
    since: datetime


class QuietRequest(BaseModel):
    mode: QuietMode
    until: datetime | None = None


@dataclass(frozen=True)
class QuietChange:
    kind: Literal["started", "ended"]
    state: QuietState
