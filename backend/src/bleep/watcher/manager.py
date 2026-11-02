"""One supervised task group per host, and the place where the detector's outcomes act.

For each host the manager runs the pull watcher (Docker events and container states), the host
probe (disk, memory, load) and the URL checks. Every signal goes through the one shared
`Detector`; what comes out becomes an incident, a logged recovery or nothing:

- `OpenIncident` and `AttachSignals` go to `IncidentRepo.open_or_attach`; the incident runner
  starts only when that call newly opened the incident.
- `Recovered` is logged with a one-line explanation from the cheap model, off the watcher's
  tick so a slow model never delays the next pull.

See docs/design/watcher.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from bleep.graph.deps import ModelFactory
from bleep.guard.log import GuardLogger
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import RecoveryEvent, Signal, Subject
from bleep.watcher import host_probe, http_checks, poller
from bleep.watcher.detector import (
    AttachSignals,
    Detector,
    Ignored,
    OpenIncident,
    Outcome,
    Recovered,
    explain_recovery,
)
from bleep.watcher.host_probe import HostProbe
from bleep.watcher.http_checks import UrlCheckStore, UrlWatcher
from bleep.watcher.poller import HostWatcher

log = logging.getLogger(__name__)

HELD_PER_SERVICE = 5  # URL failures kept per service while its host is unreachable
RESTART_BACKOFF_S = (1, 2, 5, 15, 60)
LOG_TAIL_LINES = 50
NO_EXPLANATION = "No explanation could be generated for this restart."


class _HostTasks:
    def __init__(self, watcher: HostWatcher, probe: HostProbe, urls: UrlWatcher) -> None:
        self.watcher = watcher
        self.probe = probe
        self.urls = urls


class WatcherManager:
    def __init__(
        self,
        registry: Registry,
        detector: Detector,
        repo: IncidentRepo,
        runner: IncidentRunner,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] = utcnow,
        models: ModelFactory | None = None,
        guard_log: GuardLogger | None = None,
        http: httpx.AsyncClient | None = None,
        store: UrlCheckStore | None = None,
        watch_interval_s: float = poller.INTERVAL_S,
        probe_interval_s: float = host_probe.INTERVAL_S,
        url_interval_s: float = http_checks.INTERVAL_S,
    ) -> None:
        self.registry = registry
        self.detector = detector
        self.repo = repo
        self.runner = runner
        self.bus = bus
        self._clock = clock
        self._models = models
        self._guard_log = guard_log
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._held: dict[str, dict[str, list[Signal]]] = {}  # host -> service id -> URL failures
        self._background: set[asyncio.Task[None]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self.hosts: dict[str, _HostTasks] = {
            state.config.id: _HostTasks(
                HostWatcher(
                    state.config.id,
                    registry,
                    self.sink,
                    clock,
                    watch_interval_s,
                    on_snapshot=self.snapshot,
                ),
                HostProbe(state.config.id, registry, self.sink, clock, probe_interval_s),
                UrlWatcher(
                    state.config.id, registry, self._http, self.sink, clock, store, url_interval_s
                ),
            )
            for state in registry.hosts()
        }

    async def start(self) -> None:
        """Restart unfinished runs, then watch every host. Idempotent while running."""
        if self._tasks:
            return
        await self.runner.recover_on_boot()
        for host_id, parts in self.hosts.items():
            for part, run in (
                ("watcher", parts.watcher.run),
                ("probe", parts.probe.run),
                ("urls", parts.urls.run),
            ):
                name = f"{part}-{host_id}"
                self._tasks.append(asyncio.create_task(self._supervised(name, run), name=name))

    async def stop(self) -> None:
        """Cancel the watching; runs in flight stay with the runner."""
        tasks = [*self._tasks, *self._background]
        self._tasks = []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_http:
            await self._http.aclose()

    async def drain(self) -> None:
        """Wait for the recovery explanations in flight (tests, and a clean shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
            await asyncio.sleep(0)

    @staticmethod
    async def _supervised(name: str, run: Callable[[], Awaitable[None]]) -> None:
        """Run a loop for good: if it dies of an error, log it and start it again, backing off."""
        failures = 0
        while True:
            try:
                await run()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s stopped on an error; restarting", name)
                delay = RESTART_BACKOFF_S[min(failures, len(RESTART_BACKOFF_S) - 1)]
                failures += 1
                await asyncio.sleep(delay)

    # -- what the host tasks feed in ----------------------------------------------------------

    async def sink(self, signal: Signal) -> None:
        host_id = signal.subject.host_id
        if signal.kind == "url_failed" and not self._reachable(host_id):
            if not signal.detail.get("ok", False):
                # The host is out of contact: a failing URL is a symptom of that, so hold it for
                # the host incident instead of letting each service open its own.
                await self._hold(signal)
                return
        elif signal.kind == "host_unreachable" and signal.detail.get("ok", False):
            self._held.pop(host_id, None)  # contact is back; later failures count on their own
        await self._handle(self.detector.feed(signal))

    async def snapshot(self, host_id: str, states: dict[str, list[ContainerSummary]]) -> None:
        """Judge the host's pending deaths against the listing its watcher just took.

        Only this host's deaths: a host that stops answering takes no listing, so its deaths
        stay pending until it answers again instead of being judged by its last, stale one.
        """
        await self._handle(self.detector.tick(self._clock(), states, host_id))

    def _reachable(self, host_id: str) -> bool:
        return next(h for h in self.registry.hosts() if h.config.id == host_id).reachable

    # -- URL failures on an unreachable host --------------------------------------------------

    async def _hold(self, signal: Signal) -> None:
        held = self._held.setdefault(signal.subject.host_id, {})
        failures = held.setdefault(signal.subject.service_id or "", [])
        failures.append(signal)
        del failures[:-HELD_PER_SERVICE]
        await self._attach_held(signal.subject.host_id)

    async def _attach_held(self, host_id: str) -> None:
        """Attach held URL failures to the host's open unreachable incident, if it has one.

        `open_or_attach` routes a service signal to that incident because it carries a
        `host_unreachable` signal; the check here keeps a host incident opened for another
        reason (a full disk) from being mistaken for it.
        """
        if not self._held.get(host_id):
            return
        host_incident = await self.repo.open_incident_id(Subject(kind="host", host_id=host_id))
        if host_incident is None:
            return
        signals = await self.repo.signals(host_incident)
        if not any(s.kind == "host_unreachable" for s in signals):
            return
        # Claim the held failures atomically: concurrent callers for the host interleave at the
        # awaits above, and only the first to get here may attach them.
        held = self._held.pop(host_id, None)
        if not held:
            return
        for failures in held.values():
            incident, created = await self.repo.open_or_attach(failures[0].subject, failures)
            if created:  # the host incident closed between the checks: this one is new
                await self.runner.start(incident)
        urls = sorted({str(s.detail.get("url")) for failures in held.values() for s in failures})
        text = f"URL checks failing while the host is unreachable: {', '.join(urls)}."
        await self.bus.publish(host_incident, "notice", {"text": text})

    # -- the detector's outcomes ---------------------------------------------------------------

    async def _handle(self, outcomes: list[Outcome]) -> None:
        for outcome in outcomes:
            try:
                match outcome:
                    case OpenIncident() | AttachSignals():
                        await self._open(outcome.subject, outcome.signals)
                    case Recovered():
                        self._explain_later(outcome)
                    case Ignored():
                        log.debug("detector ignored a signal: %s", outcome.reason)
            except Exception:  # one failed outcome must not drop the others
                log.exception("handling detector outcome %r failed", outcome)

    async def _open(self, subject: Subject, signals: list[Signal]) -> None:
        """Open or attach; start the run only for a newly opened incident.

        `AttachSignals` takes the same path: it normally attaches, but if the incident was
        closed in between, it opens a new one, which then needs its run.
        """
        incident, created = await self.repo.open_or_attach(subject, signals)
        if not created:
            return
        await self.runner.start(incident)
        if subject.kind == "host":
            await self._attach_held(subject.host_id)

    def _explain_later(self, recovered: Recovered) -> None:
        task = asyncio.create_task(
            self._log_recovery(recovered), name="recovery-" + recovered.container
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _log_recovery(self, recovered: Recovered) -> None:
        death = recovered.signals[0]
        host_id = death.subject.host_id
        logs = ""
        try:
            logs = await self.registry.engine(host_id).logs(recovered.container, LOG_TAIL_LINES)
        except Exception as error:  # the explanation is a nicety: the recovery is still logged
            log.warning("no logs for recovered container %s: %s", recovered.container, error)
        explanation = NO_EXPLANATION
        if self._models is not None:
            try:
                explanation = await explain_recovery(
                    self._models("cheap"), logs, self._guard_log, ""
                )
            except Exception:
                log.exception("explaining the recovery of %s failed", recovered.container)
        try:
            await self.repo.log_recovery(
                RecoveryEvent(
                    host_id=host_id,
                    service_id=death.subject.service_id,
                    container=recovered.container,
                    at=death.at,
                    explanation=explanation,
                )
            )
        except Exception:
            log.exception("logging the recovery of %s failed", recovered.container)
