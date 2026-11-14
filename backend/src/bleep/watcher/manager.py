"""One supervised task group per host, and the place where the detector's outcomes act.

For each host the manager runs the pull watcher (Docker events and container states), the host
probe (disk, memory, load) and the URL checks. Every signal goes through the one shared
`Detector`; what comes out becomes an incident, a logged recovery or nothing:

- `OpenIncident` and `AttachSignals` go to `IncidentRepo.open_or_attach`; the incident runner
  starts only when that call newly opened the incident. A failing reading (a URL check, the host
  not answering, disk, memory, load) that outlived its last incident with no pass since opens no
  new one until a hold has passed (`repeat_hold`), so a failure Bleep could not fix costs one run
  and one message, not one a minute. A service's URL waits for its last incident of any kind; a
  container still in a restart loop waits for its last death or loop incident.
- `Recovered` is logged with a one-line explanation from the cheap model, off the watcher's
  tick so a slow model never delays the next pull. The call is in the usage ledger under
  `RECOVERY_USAGE` and is skipped once the day's cost cap is spent, and for a death inside a
  crash loop whose repeat is held, which gets a fixed line.

After every poll tick and every round of URL checks each of the host's services is judged
(`watcher/health.service_health`, from the same inputs the services API uses) and kept in the
`HealthStore`, so the service page can say since when it has had its health.

A host added, changed or removed on the dashboard is put in force by `host_changed`, without a
restart: its loops start, start again on the new connection, or stop and its open incidents
are closed with a notice.

Bleep's own sleep is not a host outage: when the wall clock ran on while the monotonic clock did
not (`SleepWatch`, asked by every loop before it feeds a result), every host's outage timer starts
again and failed contact is not counted for a grace window (`woke`). A host that may sleep opens
no incident by not answering, and every tick that reaches a host closes its waiting
host-unreachable incident (`IncidentRunner.host_back`).

See docs/design/watcher.md.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import httpx
from langchain_core.language_models import BaseChatModel

from bleep.config import Settings
from bleep.graph.deps import ModelFactory
from bleep.graph.shown import english, shown_payload
from bleep.guard.log import GuardLogger
from bleep.guard.titles import title_name
from bleep.hosts.dashboard import HostChange
from bleep.hosts.registry import Registry
from bleep.hosts.url_check import UrlCheck
from bleep.incidents.bus import EventBus
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import (
    IncidentRepo,
    Repeats,
    container_fold_keys,
    fold_key,
    utcnow,
)
from bleep.incidents.runner import IncidentRunner
from bleep.llm.guard import ModelGuard
from bleep.llm.usage import BudgetExceeded, CostLedger, UsageTracker, check_daily_cap
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Incident, IncidentStatus, RecoveryEvent, Signal, Subject
from bleep.schemas.quiet import QuietChange, QuietState
from bleep.schemas.services import HostState
from bleep.schemas.settings import DEFAULT_LANGUAGE, Budgets, Language
from bleep.schemas.shown import Shown
from bleep.watcher import host_probe, http_checks, poller
from bleep.watcher.detector import (
    AttachSignals,
    Detector,
    FailingRun,
    Ignored,
    OpenIncident,
    Outcome,
    Pending,
    Recovered,
    recovery_line,
)
from bleep.watcher.health import container_health, is_asleep, service_health, url_status
from bleep.watcher.health_store import HealthStore
from bleep.watcher.host_probe import HostProbe
from bleep.watcher.http_checks import UrlCheckStore, UrlWatcher
from bleep.watcher.poller import HostWatcher
from bleep.watcher.recovery import RecoveryCheck, RecoveryWatch
from bleep.watcher.sightings import Sightings
from bleep.watcher.wake import SleepWatch

log = logging.getLogger(__name__)

HELD_PER_SERVICE = 5  # URL failures kept per service while its host is unreachable
# Wall time beyond monotonic time between two looks, in watcher intervals, that means Bleep slept.
SLEPT_AFTER_INTERVALS = 2
RESTART_BACKOFF_S = (1, 2, 5, 15, 60)
LOG_TAIL_LINES = 50
# The recovery lines Bleep writes in place of the cheap model's, stored in English and kept as
# templates to show them in the person's language (#455).
NO_EXPLANATION_SHOWN = Shown.of("recovery/no_explanation")
CAP_SPENT_SHOWN = Shown.of("recovery/cap_spent")
HELD_LOOP_SHOWN = Shown.of("recovery/held_loop")
NO_EXPLANATION = english(NO_EXPLANATION_SHOWN)
CAP_SPENT_EXPLANATION = english(CAP_SPENT_SHOWN)
HELD_LOOP_EXPLANATION = english(HELD_LOOP_SHOWN)
RECOVERY_USAGE = "recoveries"  # the usage ledger's key for recovery lines, which have no incident
REMOVED_NOTICE = Shown.of("notice/removed")
MOVED_NOTICE = Shown.of("notice/moved")
EVERY_SERVICE = "*"  # a waiting recheck of all of a host's services, known only from a listing
RUNNING_STATUSES: tuple[IncidentStatus, ...] = (
    "executing",
    "verifying",
)  # a run mid-step is left to finish
REPEAT_HOLD = timedelta(hours=1)
REPEAT_HOLD_MAX = timedelta(hours=24)


def repeat_hold(repeats: int) -> timedelta:
    """How long a failure that outlived `repeats` incidents waits before it opens the next: an
    hour after the first, doubling with each repeat, at most a day."""
    return min(REPEAT_HOLD * 2 ** min(repeats - 1, 5), REPEAT_HOLD_MAX)


def quiet_notice(name: str, state: QuietState) -> Shown:
    template = "notice/left_alone" if state.mode == "leave_alone" else "notice/snoozed"
    return Shown.of(template, name=name)


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
        language: Callable[[], Language] = lambda: DEFAULT_LANGUAGE,
        model_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        http: httpx.AsyncClient | None = None,
        store: UrlCheckStore | None = None,
        watch_interval_s: float = poller.INTERVAL_S,
        probe_interval_s: float = host_probe.INTERVAL_S,
        url_interval_s: float = http_checks.INTERVAL_S,
        quiet: QuietStore | None = None,
        health: HealthStore | None = None,
        wake_grace_ticks: int = Settings.model_fields["wake_grace_ticks"].default,
        monotonic: Callable[[], float] = time.monotonic,
        sightings: Sightings | None = None,
        check_recovery: RecoveryCheck | None = None,
    ) -> None:
        self.registry = registry
        self.detector = detector
        self.repo = repo
        self.runner = runner
        self.bus = bus
        self._clock = clock
        self._quiet = quiet
        self._health = health
        self.sightings = sightings or Sightings()
        self._models = models
        self._guard_log = guard_log
        self._ledger = ledger
        self._budgets = budgets
        self._language = language
        self._model_sleep = model_sleep
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._held: dict[str, dict[str, list[Signal]]] = {}  # host -> service id -> URL failures
        self._announced: dict[str, tuple[str, set[str]]] = {}  # host -> (incident, URLs named)
        self._deferred: dict[str, set[str]] = {}  # host -> service ids to recheck on its listing
        self._monotonic = monotonic
        self._answered: dict[str, bool] = {}  # host -> whether its last tick reached it
        self._answered_at: dict[str, datetime] = {}  # host -> its last tick that reached it
        self._wake_grace_s = watch_interval_s * wake_grace_ticks
        # Held on the monotonic clock, so a wall clock set back cannot stretch it.
        self._grace_until: float | None = None
        self.sleep_watch = SleepWatch(
            clock, monotonic, watch_interval_s * SLEPT_AFTER_INTERVALS, self.woke
        )
        self._background: set[asyncio.Task[None]] = set()
        self._store = store
        self._intervals = (watch_interval_s, probe_interval_s, url_interval_s, store)
        self._tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._started = False
        self.hosts: dict[str, _HostTasks] = {
            state.config.id: self._host_tasks(state.config.id) for state in registry.hosts()
        }
        repo.on_finished(self._fixed)
        self.recoveries = (
            None
            if check_recovery is None
            else RecoveryWatch(
                repo, runner, check_recovery, clock, self._display_name, closed=self._loops_over
            )
        )

    def _host_tasks(self, host_id: str) -> _HostTasks:
        watch_s, probe_s, url_s, store = self._intervals
        return _HostTasks(
            HostWatcher(
                host_id,
                self.registry,
                self.sink,
                self._clock,
                watch_s,
                on_snapshot=self.snapshot,
                sleep_watch=self.sleep_watch,
                sightings=self.sightings,
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
                sleep_watch=self.sleep_watch,
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
                    await self.runner.end(incident.id, REMOVED_NOTICE, ending="removed")
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
                await self.bus.publish(incident.id, "notice", shown_payload(MOVED_NOTICE))
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
        self._answered.pop(host_id, None)
        self._answered_at.pop(host_id, None)
        self.sightings.forget(host_id)
        if self.recoveries is not None:
            self.recoveries.forget_host(host_id)

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

    async def woke(self, at: datetime) -> None:
        """Bleep itself was suspended (its machine slept): what it could not see while asleep is
        no outage. Every host's outage timer and URL streak starts again, and failed contact is
        not counted until the grace window after the wake has passed, so the network can come
        back first."""
        log.info(
            "Bleep slept until %s; failed contact is not counted for %.0f s", at, self._wake_grace_s
        )
        self._grace_until = self._monotonic() + self._wake_grace_s
        self.detector.woke()

    def waking(self) -> bool:
        """Whether this is the grace window after Bleep's own wake."""
        return self._grace_until is not None and self._monotonic() < self._grace_until

    def _waking(self, signal: Signal) -> bool:
        """A failed contact inside the grace window after Bleep's own wake."""
        return (
            signal.kind in ("host_unreachable", "url_failed")
            and not signal.detail.get("ok", False)
            and self.waking()
        )

    async def sink(self, signal: Signal) -> None:
        host_id = signal.subject.host_id
        if signal.kind == "host_unreachable":
            self._answered[host_id] = bool(signal.detail.get("ok", False))
            if self._answered[host_id]:
                self._answered_at[host_id] = signal.at
                await self._host_answers(host_id)
        if self._waking(signal):
            log.debug("%s: %s not counted just after a wake", signal.subject.key, signal.kind)
            return
        if signal.kind == "url_failed" and not self._reachable(host_id):
            if not signal.detail.get("ok", False):
                # The host is out of contact: a failing URL is a symptom of that, so hold it for
                # the host incident instead of letting each service open its own.
                await self._hold(signal)
                return
        elif signal.kind == "host_unreachable" and signal.detail.get("ok", False):
            self._held.pop(host_id, None)  # contact is back; later failures count on their own
        await self._handle(self.detector.feed(signal))

    def answered(self, host_id: str) -> datetime | None:
        """When the host's last probe reached it; None when that probe failed or none ran yet."""
        return self._answered_at.get(host_id) if self._answered.get(host_id) else None

    async def _host_answers(self, host_id: str) -> None:
        """Every tick that reaches a host closes its waiting host-unreachable incident, or starts
        its run (`host_back`), not only the first after an outage: a run that parked just after
        the host came back is caught by the next tick. It is one indexed read when none waits."""
        try:
            await self.runner.host_back(host_id)
        except Exception:  # the incident stays waiting; the next tick tries again
            log.exception("closing the waiting incident of host %s failed", host_id)

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
        ones too (quiet only stops incidents), then hand the services that are healthy now, with
        every URL passing, to the recovery watch (#571). Never raises: it must not cost the
        tick."""
        if self.waking() or self._asleep_host(host_id):
            if self.recoveries is not None:
                self.recoveries.forget_host(host_id)
            return  # nothing is known about it now, and `unknown` is never stored either
        healthy: set[str] = set()
        try:
            now = self._clock()
            stored = await self._stored_checks(host_id)
            stopped = self.sightings.of(host_id).stopped_on_purpose
            for service in self.registry.services(host_id):
                urls = [
                    url_status(u.url, stored.get((service.id, u.url)), now) for u in service.urls
                ]
                statuses = [u.status for u in urls]
                health = service_health(service.containers, statuses, service.exits, stopped)
                if self._health is not None:
                    await self._health.record(host_id, service.id, health, now)
                if health == "ok" and all(status == "ok" for status in statuses):
                    healthy.add(service.id)
        except Exception:
            log.exception("recording the health of host %s's services failed", host_id)
            healthy = set()
        if self.recoveries is not None:
            # A host out of contact has a stale listing: nothing on it counts as healthy.
            await self.recoveries.judge(host_id, healthy if self._reachable(host_id) else set())

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
                await self.runner.end(incident.id, notice, ending="quiet", spare=RUNNING_STATUSES)
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
        stopped = self.sightings.of(service.host_id).stopped_on_purpose
        statuses = (u.status for u in urls)
        if service_health(service.containers, statuses, service.exits, stopped) != "down":
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
            if container_health(c, stopped_on_purpose=c.id in stopped) == "down"
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
            return title_name(service.name) if service else state.target_id
        host = self._host(state.target_id)
        return host.config.name if host else state.target_id

    def _asleep_host(self, host_id: str) -> bool:
        """Asleep as the API shows it, or already from its last failed tick: the registry marks a
        host unreachable only once its reconnect fails, a tick later."""
        host = self._host(host_id)
        if host is None or not host.config.may_sleep:
            return False
        return is_asleep(host) or self._answered.get(host_id) is False

    def _asleep(self, subject: Subject, signals: list[Signal]) -> bool:
        """A host that may sleep (a laptop) and only does not answer: that is its sleep."""
        host = self._host(subject.host_id)
        return (
            subject.kind == "host"
            and host is not None
            and host.config.may_sleep
            and all(s.kind == "host_unreachable" for s in signals)
        )

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
            subject = failures[0].subject
            incident, created = await self.repo.open_or_attach(
                subject, failures, name=self._service_name(subject)
            )
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
            text = Shown.of("notice/urls_unreachable", urls=", ".join(sorted(new)))
            await self.bus.publish(host_incident, "notice", shown_payload(text))

    # -- the detector's outcomes ---------------------------------------------------------------

    async def _handle(self, outcomes: list[Outcome]) -> None:
        for outcome in outcomes:
            try:
                match outcome:
                    case OpenIncident() | AttachSignals():
                        await self._open(outcome.subject, outcome.signals, outcome.run)
                    case Recovered():
                        self._explain_later(outcome)
                    case Ignored():
                        log.debug("detector ignored a signal: %s", outcome.reason)
            except Exception:  # one failed outcome must not drop the others
                log.exception("handling detector outcome %r failed", outcome)

    async def _open(
        self, subject: Subject, signals: list[Signal], run: FailingRun | None = None
    ) -> None:
        """Open or attach; start the run only for a newly opened incident.

        `AttachSignals` takes the same path: it normally attaches, but if the incident was
        closed in between, it opens a new one, which then needs its run.
        """
        if await self._quiet_now(subject):
            log.debug("%s is quiet: no incident for %d signal(s)", subject.key, len(signals))
            return
        if self._asleep(subject, signals):
            log.debug("%s may sleep: not answering opens no incident", subject.key)
            return
        outlived = None if run is None else await self._outlived(subject, signals, run)
        if outlived is not None and self._still_held(subject, outlived):
            return
        repeat_of = outlived.last_id if outlived is not None else None
        incident, created = await self.repo.open_or_attach(
            subject, signals, repeat_of, self._service_name(subject)
        )
        if not created:
            return
        await self.runner.start(incident)
        if subject.kind == "host":
            await self._attach_held(subject.host_id)

    async def watching(self) -> list[Pending]:
        """What the detector waits on that opens an incident once the wait is over (#358): the
        same checks `_open` makes, read without acting. A quiet subject, a host that may sleep,
        a subject whose incident is already open and a repeat still held are left out, since
        nothing would open for them."""
        found = []
        for pending in self.detector.pending():
            subject = pending.subject
            if await self._quiet_now(subject) or self._asleep(subject, pending.signals):
                continue
            if await self.repo.open_incident_id(subject) is not None:
                continue
            if pending.run is not None:
                outlived = await self._outlived(subject, pending.signals, pending.run)
                if outlived is not None and self._hold_until(outlived) is not None:
                    continue
            found.append(pending)
        return found

    def _service_name(self, subject: Subject) -> str | None:
        """The service's name as the dashboard shows it, for the incident's short name."""
        service = self.registry.service(subject.service_id) if subject.service_id else None
        return service.name if service is not None else None

    async def _outlived(
        self, subject: Subject, signals: list[Signal], run: FailingRun
    ) -> Repeats | None:
        """The finished incidents a failing reading outlived: opened since it last passed. None
        while the subject has an incident open, so its failures attach to it as before.

        For a service that is any incident of it: its URL failing after an `unhealthy` incident
        is the same broken service. A host's readings measure apart (a full disk says nothing of
        memory), so for a host only incidents of the same kind count. A container still in a
        restart loop outlived its own deaths and loops, never another container's.

        A failure Bleep has not seen pass since it started may be older than Bleep's memory, so
        the incidents of the longest hold before count too: a restart costs no new run either.
        """
        if await self.repo.open_incident_id(subject) is not None:
            return None
        since = run.passed
        if since is None:
            since = min(run.since, self._clock() - REPEAT_HOLD_MAX)
        keys = None
        if run.container is not None:
            keys = container_fold_keys(subject, run.container)
        elif subject.kind == "host":
            keys = [fold_key(subject, signals)]
        return await self.repo.finished_since(subject, since, keys)

    async def _fixed(self, incident_id: str) -> None:
        """A fix Bleep verified (`resolved`) is the pass the detector cannot see for a container:
        its loop ends there, so a relapse opens at once."""
        incident = await self.repo.get(incident_id)
        if incident is None or incident.status != "resolved" or incident.subject.kind != "service":
            return
        await self._loops_over(incident, incident.closed_at)

    async def _loops_over(self, incident: Incident, at: datetime | None = None) -> None:
        """End the loops of the incident's containers in the detector, at `at` (now when not
        given): a verified fix (`_fixed`), or a service held healthy again on its own and
        checked by Verify's checks (#571), is the pass the detector cannot see."""
        at = at or self._clock()
        for container in {s.container for s in await self.repo.signals(incident.id)}:
            if container is not None:
                self.detector.recovered(incident.subject.host_id, container, at)

    def _display_name(self, subject: Subject) -> str:
        """The service's name as a notice shows it."""
        name = self._service_name(subject)
        return title_name(name) if name is not None else subject.service_id or subject.host_id

    def _hold_until(self, outlived: Repeats) -> datetime | None:
        """Until when a failure that outlived these incidents waits; None once that is over."""
        until = outlived.last_closed + repeat_hold(outlived.count)
        return until if self._clock() < until else None

    def _still_held(self, subject: Subject, outlived: Repeats) -> bool:
        """Whether the failure must still wait for the hold of the incidents it outlived."""
        until = self._hold_until(outlived)
        if until is None:
            return False
        log.debug(
            "%s still fails after its last incident: none opens before %s", subject.key, until
        )
        return True

    def _explain_later(self, recovered: Recovered) -> None:
        task = asyncio.create_task(
            self._log_recovery(recovered), name="recovery-" + recovered.container
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _explain(self, model: BaseChatModel, logs: str) -> str | Shown:
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
        return await recovery_line(
            model, logs, self._guard_log, "", config, guard, self._language()
        )

    async def _log_recovery(self, recovered: Recovered) -> None:
        death = recovered.signals[0]
        if await self._in_held_loop(recovered):
            line: str | Shown = HELD_LOOP_SHOWN
        else:
            line = await self._recovery_line(recovered)
        fixed = line if isinstance(line, Shown) else None
        try:
            await self.repo.log_recovery(
                RecoveryEvent(
                    host_id=death.subject.host_id,
                    service_id=death.subject.service_id,
                    container=recovered.container,
                    at=death.at,
                    explanation=line if fixed is None else english(fixed),
                    explanation_shown=fixed,
                )
            )
        except Exception:
            log.exception("logging the recovery of %s failed", recovered.container)

    async def _in_held_loop(self, recovered: Recovered) -> bool:
        """Whether the death is one more of a crash loop whose repeat is held: it opens nothing,
        so a line from the model would cost a call per death for as long as the hold lasts."""
        if recovered.run is None:
            return False
        subject = recovered.signals[0].subject
        try:
            outlived = await self._outlived(subject, recovered.signals, recovered.run)
        except Exception:
            log.exception("checking the loop of recovered container %s failed", recovered.container)
            return False
        return outlived is not None and self._still_held(subject, outlived)

    async def _recovery_line(self, recovered: Recovered) -> str | Shown:
        logs = ""
        try:
            engine = self.registry.engine(recovered.signals[0].subject.host_id)
            logs = await engine.logs(recovered.container, LOG_TAIL_LINES)
        except Exception as error:  # the explanation is a nicety: the recovery is still logged
            log.warning("no logs for recovered container %s: %s", recovered.container, error)
        if self._models is None:
            return NO_EXPLANATION_SHOWN
        try:
            return await self._explain(self._models("cheap"), logs)
        except BudgetExceeded:
            return CAP_SPENT_SHOWN
        except Exception:
            log.exception("explaining the recovery of %s failed", recovered.container)
            return NO_EXPLANATION_SHOWN
