"""One supervised task group per host, and the place where the detector's outcomes act.

For each host the manager runs the pull watcher (Docker events and container states), the host
probe (disk, memory, load) and the URL checks. Every signal goes through the one shared
`Detector`; what comes out becomes an incident, a logged recovery or nothing:

- `OpenIncident` and `AttachSignals` go to `IncidentRepo.open_or_attach`; the incident runner
  starts only when that call newly opened the incident.
- `Recovered` is logged with a one-line explanation from the cheap model, off the watcher's
  tick so a slow model never delays the next pull. The call is in the usage ledger under
  `RECOVERY_USAGE` and is skipped once the day's cost cap is spent.

A host added, changed or removed on the dashboard is put in force by `host_changed`, without a
restart: its loops start, start again on the new connection, or stop and its open incidents
are closed with a notice.

See docs/design/watcher.md.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
from langchain_core.language_models import BaseChatModel

from bleep.graph.deps import ModelFactory
from bleep.guard.log import GuardLogger
from bleep.hosts.dashboard import HostChange
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.llm.guard import ModelGuard
from bleep.llm.usage import BudgetExceeded, CostLedger, UsageTracker, check_daily_cap
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import RecoveryEvent, Signal, Subject
from bleep.schemas.settings import Budgets
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
CAP_SPENT_EXPLANATION = "No explanation: the daily cost cap is spent."
RECOVERY_USAGE = "recoveries"  # the usage ledger's key for recovery lines, which have no incident
REMOVED_NOTICE = "Closed: the host was removed from Bleep on the dashboard, so it is not watched."
MOVED_NOTICE = (
    "The host's address, port or user was changed on the dashboard: what Bleep reads from here on "
    "comes from the new connection, which may be another machine."
)


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
        ledger: CostLedger | None = None,
        budgets: Callable[[], Budgets] | None = None,
        model_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
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
        self._ledger = ledger
        self._budgets = budgets
        self._model_sleep = model_sleep
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._held: dict[str, dict[str, list[Signal]]] = {}  # host -> service id -> URL failures
        self._announced: dict[str, tuple[str, set[str]]] = {}  # host -> (incident, URLs named)
        self._background: set[asyncio.Task[None]] = set()
        self._intervals = (watch_interval_s, probe_interval_s, url_interval_s, store)
        self._tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._started = False
        self.hosts: dict[str, _HostTasks] = {
            state.config.id: self._host_tasks(state.config.id) for state in registry.hosts()
        }

    def _host_tasks(self, host_id: str) -> _HostTasks:
        watch_s, probe_s, url_s, store = self._intervals
        return _HostTasks(
            HostWatcher(
                host_id, self.registry, self.sink, self._clock, watch_s, on_snapshot=self.snapshot
            ),
            HostProbe(host_id, self.registry, self.sink, self._clock, probe_s),
            UrlWatcher(host_id, self.registry, self._http, self.sink, self._clock, store, url_s),
        )

    async def start(self) -> None:
        """Restart unfinished runs, then watch every host. Idempotent while running."""
        if self._started:
            return
        self._started = True
        await self.runner.recover_on_boot()
        for host_id in self.hosts:
            self._run(host_id)

    def _run(self, host_id: str) -> None:
        parts = self.hosts[host_id]
        tasks = self._tasks.setdefault(host_id, [])
        for part, run in (
            ("watcher", parts.watcher.run),
            ("probe", parts.probe.run),
            ("urls", parts.urls.run),
        ):
            name = f"{part}-{host_id}"
            tasks.append(asyncio.create_task(self._supervised(name, run), name=name))

    async def stop(self) -> None:
        """Cancel the watching; runs in flight stay with the runner."""
        tasks = [task for host in self._tasks.values() for task in host] + [*self._background]
        self._tasks = {}
        self._started = False
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_http:
            await self._http.aclose()

    async def host_changed(self, change: HostChange) -> None:
        """Put a host added, changed or removed on the dashboard in force. A new connection (or
        key) starts the host's loops again from nothing, so a listing of another machine is never
        diffed against the old one's; a new address, port or user also drops the detector's state
        for the host and puts a notice on its open incidents; a removed host's open incidents are
        closed with a notice."""
        host_id = change.host_id
        if change.kind == "renamed" and change.config is not None:
            await self.registry.replace(change.config, reconnect=False)
            return
        await self._stop_host(host_id)
        if change.config is None:
            try:
                for incident in await self.repo.open_on_host(host_id):
                    await self.runner.end(incident.id, REMOVED_NOTICE)
            finally:  # the host goes whatever happened above, so it can be added again
                await self.registry.remove(host_id)
                self._forget(host_id)
            return
        if change.moved:
            # Possibly another machine: nothing pending on the old one may open an incident here.
            self._forget(host_id)
            for incident in await self.repo.open_on_host(host_id):
                await self.bus.publish(incident.id, "notice", {"text": MOVED_NOTICE})
        if change.kind == "added":
            self.registry.add(change.config)
        else:
            await self.registry.replace(change.config, reconnect=True)
        self.hosts[host_id] = self._host_tasks(host_id)
        if self._started:
            self._run(host_id)

    def _forget(self, host_id: str) -> None:
        self.detector.forget_host(host_id)
        self._held.pop(host_id, None)
        self._announced.pop(host_id, None)

    async def _stop_host(self, host_id: str) -> None:
        self.hosts.pop(host_id, None)
        tasks = self._tasks.pop(host_id, [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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
        return any(h.reachable for h in self.registry.hosts() if h.config.id == host_id)

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
        urls = {str(s.detail.get("url")) for failures in held.values() for s in failures}
        await self._announce_urls(host_id, host_incident, urls)

    async def _announce_urls(self, host_id: str, host_incident: str, urls: set[str]) -> None:
        """Name failing URLs on the host incident's timeline once, not on every check: the
        report is built from these events, and a long outage checks each URL every 30 s."""
        incident, named = self._announced.get(host_id, ("", set()))
        if incident != host_incident:
            named = set()
        new = urls - named
        self._announced[host_id] = (host_incident, named | urls)
        if new:
            text = f"URL checks failing while the host is unreachable: {', '.join(sorted(new))}."
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

    async def _explain(self, model: BaseChatModel, logs: str) -> str:
        """The model's line, counted in the usage ledger and refused once the daily cap is spent,
        like an incident's calls."""
        config = None
        if self._ledger is not None:
            # Without logs `explain_recovery` answers without the model, so the cap is not asked.
            if self._budgets is not None and logs.strip():
                await check_daily_cap(self._ledger, self._budgets())
            config = {"callbacks": [UsageTracker(RECOVERY_USAGE, self._ledger)]}
        timeout_s = (self._budgets or Budgets)().model_timeout_s
        guard = ModelGuard(timeout_s, sleep=self._model_sleep)
        return await explain_recovery(model, logs, self._guard_log, "", config, guard)

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
                explanation = await self._explain(self._models("cheap"), logs)
            except BudgetExceeded:
                explanation = CAP_SPENT_EXPLANATION
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
