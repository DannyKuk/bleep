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

from bleep.hosts.registry import Registry
from bleep.schemas.docker import HostBasics
from bleep.schemas.incidents import Signal, Subject

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
        signals: list[Signal] = []
        if basics.disks:
            worst = max(basics.disks, key=lambda d: d.used_percent)
            detail = {"percent": worst.used_percent, "mount": worst.mount}
            signals.append(Signal(subject=subject, kind="disk", at=at, detail=detail))
        if basics.mem_total and basics.mem_available is not None:
            percent = basics.mem_available / basics.mem_total * 100
            signals.append(
                Signal(subject=subject, kind="memory", at=at, detail={"available_percent": percent})
            )
        if basics.load15 is not None and basics.cpus:
            per_cpu = basics.load15 / basics.cpus
            signals.append(
                Signal(subject=subject, kind="load", at=at, detail={"load_per_cpu": per_cpu})
            )
        return signals
