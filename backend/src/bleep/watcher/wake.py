"""Noticing that Bleep itself was suspended (its machine slept, its container was paused).

While the process is suspended the wall clock runs on and the monotonic clock does not (macOS,
Linux and the Linux VM Docker Desktop runs Bleep in), so a gap between the two, measured from the
last look by any loop, is time Bleep could not see. See docs/design/watcher.md.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

Wake = Callable[[datetime], Awaitable[None]]


class SleepWatch:
    """Shared by every watching loop. Each calls `check()` before it feeds a result, so a result
    taken across a sleep (a tick that was inside an SSH connect when the lid closed) is fed only
    after the wake is known. A wall clock set back (NTP) is never a wake."""

    def __init__(
        self,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
        threshold_s: float,
        on_wake: Wake,
    ) -> None:
        self._clock = clock
        self._monotonic = monotonic
        self._threshold_s = threshold_s
        self._on_wake = on_wake
        self._mark: tuple[datetime, float] | None = None

    async def check(self) -> bool:
        """Whether Bleep slept since the last look; `on_wake` has heard it when this returns."""
        wall, mono = self._clock(), self._monotonic()
        mark, self._mark = self._mark, (wall, mono)
        if mark is None:
            return False
        slept = (wall - mark[0]).total_seconds() - (mono - mark[1])
        if slept <= self._threshold_s:
            return False
        await self._on_wake(wall)
        return True
