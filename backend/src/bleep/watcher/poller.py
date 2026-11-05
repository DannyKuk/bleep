"""The pull-model watcher: one tick per host every 15 s.

Each tick reconnects if the host is down, reads Docker events since the cursor, lists the
containers, and turns both into signals. A dropped connection or a sleeping host is a non-event:
the tick fails, nothing is marked dead, the cursor stays, and the next tick replays the gap.
No model is called here (docs/design/watcher.md).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from bleep.hosts.engine import EngineError
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from bleep.watcher.events import EventKey, diff, event_key, translate
from bleep.watcher.wake import SleepWatch

log = logging.getLogger(__name__)

INTERVAL_S = 15
TICK_TIMEOUT_S = 60  # a hung call fails the tick instead of stopping the loop
SEEN_KEEP_S = 120  # how long an event identity is remembered, past the cursor

Sink = Callable[[Signal], Awaitable[None]]
Snapshot = Callable[[str, dict[str, list[ContainerSummary]]], Awaitable[None]]


class HostWatcher:
    """Watches one host. `tick()` never raises; `run()` ticks until cancelled.

    `sink` receives every signal, including the host's `host_unreachable` probe result on each
    tick (`ok` says whether the tick reached the host). `on_snapshot`, when given, receives the
    host's containers grouped by service id after every successful tick, which is what the
    detector's `tick` wants. `sleep_watch`, when given, is asked before the tick pulls and again
    before its probe result goes out, so a wake of Bleep's own (also one in the middle of the
    tick) is known before anything the tick saw is fed.
    """

    def __init__(
        self,
        host_id: str,
        registry: Registry,
        sink: Sink,
        clock: Callable[[], datetime],
        interval_s: float = INTERVAL_S,
        on_snapshot: Snapshot | None = None,
        sleep_watch: SleepWatch | None = None,
    ) -> None:
        self.host_id = host_id
        self.interval_s = interval_s
        self._registry = registry
        self._sink = sink
        self._clock = clock
        self._on_snapshot = on_snapshot
        self._sleep_watch = sleep_watch
        self._cursor = clock()
        self._previous: list[ContainerSummary] | None = None
        self._service_of: dict[str, str] = {}  # container id -> service id, as of last tick
        self._seen: dict[EventKey, datetime] = {}
        self._covered: dict[str, datetime] = {}  # container id -> death the diff reported at
        self._needs_reconnect = False

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> bool:
        """One pull. Returns whether it succeeded; a failure emits nothing but the probe result."""
        await self._notice_sleep()
        try:
            async with asyncio.timeout(TICK_TIMEOUT_S):
                ok = await self._pull()
        except Exception:
            log.exception("watcher tick for host %s failed", self.host_id)
            ok = False
        await self._notice_sleep()
        await self._probe(ok)
        return ok

    async def _notice_sleep(self) -> None:
        if self._sleep_watch is None:
            return
        try:
            await self._sleep_watch.check()
        except Exception:
            log.exception("checking for a sleep failed on host %s", self.host_id)

    async def _probe(self, ok: bool) -> None:
        signal = Signal(
            subject=Subject(kind="host", host_id=self.host_id),
            kind="host_unreachable",
            at=self._clock(),
            detail={"ok": ok},
        )
        await self._emit(signal)

    def _reachable(self) -> bool:
        return next(h for h in self._registry.hosts() if h.config.id == self.host_id).reachable

    async def _emit(self, signal: Signal) -> None:
        try:
            await self._sink(signal)
        except Exception:  # one bad signal must not drop the rest of the tick
            log.exception("signal sink failed for host %s", self.host_id)

    async def _pull(self) -> bool:
        reg = self._registry
        if not self._reachable() or self._needs_reconnect:
            if not await reg.reconnect(self.host_id):
                return False
            self._needs_reconnect = False
        until = self._clock()
        try:
            events = await reg.engine(self.host_id).events(self._cursor, until)
        except EngineError:
            self._needs_reconnect = True  # reconnect on the next tick
            return False
        await reg.refresh(self.host_id)  # the registry keeps the API's snapshot current too
        listed = max(until, self._clock())  # events up to here may describe what the listing shows
        current = reg.snapshot(self.host_id)
        if not self._reachable() or current is None:
            self._needs_reconnect = True
            return False

        # Nothing above changed watcher state: from here the tick cannot fail.
        fresh = [e for e in events if event_key(e) not in self._seen]
        by_id = {c.id: c for c in [*(self._previous or []), *current]}
        service_of = dict(self._service_of)
        for service in reg.services(self.host_id):
            service_of.update({c.id: service.id for c in service.containers})

        def subject_of(name: str, container_id: str | None) -> Subject:
            service_id = service_of.get(container_id or "") or f"{self.host_id}/container:{name}"
            return Subject(kind="service", host_id=self.host_id, service_id=service_id)

        translation = translate(fresh, by_id, subject_of, self._covered)
        signals = translation.signals
        if self._previous is not None:
            deliberate = {
                e.container_id for e in fresh if e.action in ("stop", "destroy") and e.container_id
            }
            reported = diff(
                self._previous, current, until, subject_of, translation.died_ids, deliberate
            )
            for signal in reported:
                cid = next(c.id for c in self._previous if c.name == signal.container)
                self._covered[cid] = listed
            signals = [*signals, *reported]

        self._previous = current
        grouped = _by_service(reg, self.host_id)
        self._service_of = {c.id: sid for sid, cs in grouped.items() for c in cs}
        self._cursor = until
        keep = until - timedelta(seconds=SEEN_KEEP_S)
        self._seen = {k: t for k, t in self._seen.items() if t >= keep}
        self._seen.update({event_key(e): e.time for e in fresh})
        self._covered = {c: t for c, t in self._covered.items() if t >= keep}

        for signal in signals:
            await self._emit(signal)
        if self._on_snapshot is not None:
            try:
                await self._on_snapshot(self.host_id, grouped)
            except Exception:
                log.exception("snapshot hook failed for host %s", self.host_id)
        return True


def _by_service(reg: Registry, host_id: str) -> dict[str, list[ContainerSummary]]:
    return {s.id: s.containers for s in reg.services(host_id)}
