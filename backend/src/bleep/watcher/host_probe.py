"""Disk, memory and load of one host, turned into signals once per fresh reading.

The registry already reads host basics about once a minute (`hosts/shell.py` over ssh), so the
probe reads what the registry holds instead of running the commands a second time. A reading is
fed once: the registry swaps in a new `HostBasics` object per successful read, so an unchanged
object means the read failed or has not run again, and feeding it would count one poll twice.
`host_unreachable` is not fed here: the pull watcher sends it on every tick (see watcher.md).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from bleep.hosts.registry import Registry
from bleep.schemas.docker import HostBasics
from bleep.schemas.incidents import Signal, SignalKind, Subject

log = logging.getLogger(__name__)

INTERVAL_S = 60

Sink = Callable[[Signal], Awaitable[None]]


class HostProbe:
    def __init__(
        self,
        host_id: str,
        registry: Registry,
        sink: Sink,
        clock: Callable[[], datetime],
        interval_s: float = INTERVAL_S,
    ) -> None:
        self.host_id = host_id
        self.interval_s = interval_s
        self._registry = registry
        self._sink = sink
        self._clock = clock
        self._last: HostBasics | None = None

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> list[Signal]:
        """Feed `disk`, `memory` and `load` from the host's latest unseen reading, if any."""
        state = next(h for h in self._registry.hosts() if h.config.id == self.host_id)
        basics = state.basics
        if not state.reachable or basics is None or basics is self._last:
            return []
        self._last = basics
        signals = self._signals(basics)
        for signal in signals:
            try:
                await self._sink(signal)
            except Exception:  # one bad signal must not drop the rest
                log.exception("signal sink failed for host %s", self.host_id)
        return signals

    def _signals(self, basics: HostBasics) -> list[Signal]:
        subject = Subject(kind="host", host_id=self.host_id)
        at = self._clock()
        return [
            Signal(subject=subject, kind=kind, at=at, detail=detail)
            for kind, detail in readings(basics).items()
        ]


def readings(basics: HostBasics, *, load_minutes: Literal[1, 15] = 15) -> dict[SignalKind, dict]:
    """The disk, memory and load details of one reading, keyed by signal kind, as the detector
    reads them (`detector.past_threshold`); a kind the host did not report is left out. Disk is
    the fullest mount, load is per CPU over the last `load_minutes`."""
    found: dict[SignalKind, dict] = {}
    if basics.disks:
        worst = max(basics.disks, key=lambda d: d.used_percent)
        found["disk"] = {"percent": worst.used_percent, "mount": worst.mount}
    if basics.mem_total and basics.mem_available is not None:
        found["memory"] = {"available_percent": basics.mem_available / basics.mem_total * 100}
    load = basics.load1 if load_minutes == 1 else basics.load15
    if load is not None and basics.cpus:
        found["load"] = {"load_per_cpu": load / basics.cpus}
    return found
