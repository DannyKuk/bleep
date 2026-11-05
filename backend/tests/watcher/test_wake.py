from datetime import UTC, datetime, timedelta

from bleep.watcher.wake import SleepWatch

T0 = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)


class Clocks:
    def __init__(self) -> None:
        self.wall, self.mono = T0, 0.0
        self.wakes: list[datetime] = []

    def run(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds

    async def woke(self, at: datetime) -> None:
        self.wakes.append(at)

    def watch(self) -> SleepWatch:
        return SleepWatch(lambda: self.wall, lambda: self.mono, 30, self.woke)


async def test_wall_time_beyond_monotonic_time_is_a_sleep_and_is_heard_once():
    c = Clocks()
    watch = c.watch()
    assert await watch.check() is False  # the first look only sets the mark
    c.run(600)  # ten busy minutes, or a stalled event loop: both clocks move
    assert await watch.check() is False
    c.wall += timedelta(hours=8)  # suspended: only the wall clock moves
    assert await watch.check() is True
    assert c.wakes == [c.wall]
    c.run(15)
    assert await watch.check() is False


async def test_a_short_gap_and_a_wall_clock_set_back_are_no_sleep():
    c = Clocks()
    watch = c.watch()
    await watch.check()
    c.wall += timedelta(seconds=30)  # at the threshold: not over it
    assert await watch.check() is False
    c.wall -= timedelta(hours=1)  # NTP steps the clock back
    assert await watch.check() is False
    assert c.wakes == []
