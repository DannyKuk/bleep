"""Glue between the host watchers and the detector until the manager (Task 15) exists.

One `Feed` serves every host: it feeds each signal to the shared `Detector`, ticks the detector
with the containers of all hosts after each host's tick, and logs what comes out.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal
from bleep.watcher.detector import Detector, Outcome

log = logging.getLogger(__name__)


class Feed:
    def __init__(self, detector: Detector, clock: Callable[[], datetime]) -> None:
        self.detector = detector
        self._clock = clock
        self._states: dict[str, dict[str, list[ContainerSummary]]] = {}  # host -> service -> cs

    async def sink(self, signal: Signal) -> None:
        self._record(self.detector.feed(signal))

    async def snapshot(self, host_id: str, states: dict[str, list[ContainerSummary]]) -> None:
        self._states[host_id] = states
        merged = {sid: cs for host in self._states.values() for sid, cs in host.items()}
        self._record(self.detector.tick(self._clock(), merged))

    def _record(self, outcomes: list[Outcome]) -> None:
        for outcome in outcomes:
            log.info("detector: %r", outcome)
