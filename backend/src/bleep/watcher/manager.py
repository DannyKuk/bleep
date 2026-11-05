"""One supervised task group per host, and the place where the detector's outcomes act.

For each host the manager runs the pull watcher (Docker events and container states), the host
probe (disk, memory, load) and the URL checks. Every signal goes through the one shared
`Detector`; what comes out becomes an incident, a logged recovery or nothing:

- `OpenIncident` and `AttachSignals` go to `IncidentRepo.open_or_attach`; the incident runner
  starts only when that call newly opened the incident.
- `Recovered` is logged with a one-line explanation from the cheap model, off the watcher's
  tick so a slow model never delays the next pull. The call is in the usage ledger under
  `RECOVERY_USAGE` and is skipped once the day's cost cap is spent.

After every poll tick and every round of URL checks each of the host's services is judged
(`watcher/health.service_health`, from the same inputs the services API uses) and kept in the
`HealthStore`, so the service page can say since when it has had its health.

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
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo, utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.llm.guard import ModelGuard
from bleep.llm.usage import BudgetExceeded, CostLedger, UsageTracker, check_daily_cap
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Incident, IncidentStatus, RecoveryEvent, Signal, Subject
from bleep.schemas.quiet import QuietChange, QuietState
from bleep.schemas.services import HostState
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
from bleep.watcher.health import container_health, service_health, url_status
from bleep.watcher.health_store import HealthStore
from bleep.watcher.host_probe import HostProbe
from bleep.watcher.http_checks import UrlCheck, UrlCheckStore, UrlWatcher
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
LEFT_ALONE_NOTICE = "Closed: you told me to leave {name} alone."
SNOOZED_NOTICE = "Closed: you snoozed {name}; I look at it again when the snooze ends."
EVERY_SERVICE = "*"  # a waiting recheck of all of a host's services, known only from a listing
RUNNING_STATUSES: tuple[IncidentStatus, ...] = (
    "executing",
    "verifying",
)  # a run mid-step is left to finish


def quiet_notice(name: str, state: QuietState) -> str:
    template = LEFT_ALONE_NOTICE if state.mode == "leave_alone" else SNOOZED_NOTICE
    return template.format(name=name)


def _host_of(service_id: str) -> str:
    return service_id.split("/", 1)[0]


def _subject_of(state: QuietState) -> Subject:
    if state.scope == "host":
        return Subject(kind="host", host_id=state.target_id)
    return Subject(kind="service", host_id=_host_of(state.target_id), service_id=state.target_id)


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
        quiet: QuietStore | None = None,
        health: HealthStore | None = None,
    ) -> None:
        self.registry = registry
        self.detector = detector
        self.repo = repo
        self.runner = runner
        self.bus = bus
        self._clock = clock
        self._quiet = quiet
        self._health = health
        self._models = models
        self._guard_log = guard_log
        self._ledger = ledger
        self._budgets = budgets
        self._model_sleep = model_sleep
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._held: dict[str, dict[str, list[Signal]]] = {}  # host -> service id -> URL failures
        self._announced: dict[str, tuple[str, set[str]]] = {}  # host -> (incident, URLs named)
        self._deferred: dict[str, set[str]] = {}  # host -> service ids to recheck on its listing
        self._background: set[asyncio.Task[None]] = set()
        self._store = store
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
            UrlWatcher(
                host_id,
                self.registry,
                self._http,
                self.sink,
                self._clock,
                store,
                url_s,
                on_round=self.record_health,
            ),
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
                if self._quiet is not None:  # one re-added under this id starts out watched
                    await self._quiet.forget_host(host_id)
                if self._health is not None:
                    await self._health.forget_host(host_id)
            return
        if change.moved:
            # Possibly another machine: nothing pending on the old one may open an incident here,
            # and the old one's health says nothing about since when this one has had its own.
            self._forget(host_id)
            if self._health is not None:
                await self._health.forget_host(host_id)
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
        self._deferred.pop(host_id, None)

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
        The rechecks that waited for this listing run after them.
        """
        await self._handle(self.detector.tick(self._clock(), states, host_id))
        await self._run_deferred(host_id)
        await self.record_health(host_id)

    async def record_health(self, host_id: str) -> None:
        """Judge each of the host's services as the services API does and keep the result, quiet
        ones too (quiet only stops incidents). Never raises: it must not cost the tick."""
        if self._health is None:
            return
        try:
            now = self._clock()
            stored = await self._stored_checks(host_id)
            for service in self.registry.services(host_id):
                urls = [
                    url_status(u.url, stored.get((service.id, u.url)), now) for u in service.urls
                ]
                health = service_health(service.containers, (u.status for u in urls))
                await self._health.record(host_id, service.id, health, now)
        except Exception:
            log.exception("recording the health of host %s's services failed", host_id)

    async def _stored_checks(
        self, host_id: str
    ) -> dict[tuple[str, str], tuple[UrlCheck, datetime]]:
        """The host's latest URL results by (service id, url)."""
        latest = await self._store.latest(host_id) if self._store is not None else []
        return {(sid, check.url): (check, at) for sid, check, at in latest}

    async def _quiet_now(self, subject: Subject) -> bool:
        return self._quiet is not None and await self._quiet.is_quiet(subject)

    async def quiet_changed(self, change: QuietChange) -> None:
        """A service or host was snoozed or left alone: close its unfinished incidents with a
        notice. Starting again on a quiet that is already in force finds nothing left to close.
        When a quiet ends, the subject is judged as it is now."""
        state = change.state
        if change.kind == "ended":
            await self.recheck(_subject_of(state))
            return
        self._drop_held(state)
        notice = quiet_notice(self._quiet_name(state), state)
        for incident in await self._incidents_of(state):
            try:
                await self.runner.end(incident.id, notice, spare=RUNNING_STATUSES)
            except Exception:  # one incident that cannot be closed must not keep the others open
                log.exception("closing incident %s for a quiet failed", incident.id)

    async def recheck(self, subject: Subject, *, with_urls: bool = True) -> None:
        """Judge a subject whose quiet just ended by what is true now, not by what happened while
        it was quiet: only a `down` service or an unreachable host opens an incident. Whatever is
        still quiet (a service quiet on its own under a host whose quiet ended) is left alone.

        A host that cannot be reached has no listing to judge its services by, so their recheck
        waits for its next one (`snapshot`); an unreachable host opens its host incident now.
        """
        if await self._quiet_now(subject):
            return
        if self._host(subject.host_id) is None:
            return
        if not self._reachable(subject.host_id):
            await self._recheck_unreachable(subject)
            return
        if subject.kind == "host":
            for service in self.registry.services(subject.host_id):
                await self._recheck_service(service.id, with_urls)
            return
        signals = await self._down_signals(subject, with_urls)
        if signals:
            await self._open(subject, signals)

    async def _recheck_unreachable(self, subject: Subject) -> None:
        waiting = self._deferred.setdefault(subject.host_id, set())
        if subject.kind == "service":
            waiting.add(subject.service_id or "")
            return
        waiting.add(EVERY_SERVICE)
        signal = Signal(
            kind="host_unreachable", subject=subject, at=self._clock(), detail={"ok": False}
        )
        await self._open(subject, [signal])

    async def _run_deferred(self, host_id: str) -> None:
        """Recheck what waited for this host's listing, once. Its URL results are left out: they
        were taken while the host was unreachable, and the live checks judge the URLs from here."""
        waiting = self._deferred.pop(host_id, set())
        if EVERY_SERVICE in waiting:
            waiting = (waiting - {EVERY_SERVICE}) | {s.id for s in self.registry.services(host_id)}
        for service_id in sorted(waiting):
            await self._recheck_service(service_id, with_urls=False)

    async def _recheck_service(self, service_id: str, with_urls: bool) -> None:
        subject = Subject(kind="service", host_id=_host_of(service_id), service_id=service_id)
        try:
            await self.recheck(subject, with_urls=with_urls)
        except Exception:  # one service that cannot be judged must not stop the others
            log.exception("rechecking %s after its quiet failed", service_id)

    async def _down_signals(self, subject: Subject, with_urls: bool) -> list[Signal]:
        service = self.registry.service(subject.service_id or "")
        if service is None:
            return []
        now = self._clock()
        stored = await self._stored_checks(service.host_id) if with_urls else {}
        configured = service.urls if with_urls else []
        urls = [url_status(u.url, stored.get((service.id, u.url)), now) for u in configured]
        if service_health(service.containers, (u.status for u in urls)) != "down":
            return []
        signals = [
            Signal(
                kind="down_past_grace",
                subject=subject,
                at=now,
                container=c.name,
                detail={"state": c.state, "exit_code": c.exit_code},
            )
            for c in service.containers
            if container_health(c) == "down"
        ]
        signals += [
            Signal(kind="url_failed", subject=subject, at=now, detail={"url": u.url})
            for u in urls
            if u.status == "failing"
        ]
        return signals

    def _drop_held(self, state: QuietState) -> None:
        if state.scope == "host":
            self._held.pop(state.target_id, None)
            return
        held = self._held.get(_host_of(state.target_id))
        if held is not None:
            held.pop(state.target_id, None)

    async def _incidents_of(self, state: QuietState) -> list[Incident]:
        if state.scope == "host":
            return await self.repo.open_on_host(state.target_id)
        subject = _subject_of(state)
        incident_id = await self.repo.open_incident_id(subject)
        incident = await self.repo.get(incident_id) if incident_id else None
        return [incident] if incident else []

    def _quiet_name(self, state: QuietState) -> str:
        if state.scope == "service":
            service = self.registry.service(state.target_id)
            return service.name if service else state.target_id
        host = self._host(state.target_id)
        return host.config.name if host else state.target_id

    def _host(self, host_id: str) -> HostState | None:
        return next((h for h in self.registry.hosts() if h.config.id == host_id), None)

    def _reachable(self, host_id: str) -> bool:
        host = self._host(host_id)
        return host is not None and host.reachable

    # -- URL failures on an unreachable host --------------------------------------------------

    async def _hold(self, signal: Signal) -> None:
        if await self._quiet_now(signal.subject):
            return
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
        held = {sid: f for sid, f in held.items() if not await self._quiet_now(f[0].subject)}
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
        if await self._quiet_now(subject):
            log.debug("%s is quiet: no incident for %d signal(s)", subject.key, len(signals))
            return
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
