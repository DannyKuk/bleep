import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from langchain_core.messages import AIMessage

from bleep import prompts
from bleep.graph.shown import english
from bleep.hosts.config import HostConfig, ServiceOverrides, UrlCheckConfig
from bleep.hosts.dashboard import HostChange
from bleep.hosts.engine import EngineError
from bleep.hosts.registry import Registry
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.bus import EventBus
from bleep.incidents.looks import Looks
from bleep.incidents.quiet import QuietStore
from bleep.incidents.repository import IncidentRepo
from bleep.llm.usage import CostLedger
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.graph import PlanStep, Proposal
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.quiet import QuietState
from bleep.schemas.settings import Budgets, Language, Thresholds
from bleep.schemas.shown import Shown
from bleep.store.db import Database
from bleep.watcher import manager as manager_module
from bleep.watcher.detector import NO_LOGS_LINE, AttachSignals, Detector, OpenIncident
from bleep.watcher.health_store import HealthStore
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import (
    CAP_SPENT_EXPLANATION,
    HELD_LOOP_EXPLANATION,
    NO_EXPLANATION,
    RECOVERY_USAGE,
    REMOVED_NOTICE,
    WatcherManager,
    quiet_notice,
    repeat_hold,
)
from fakes import FakeChatModel, FakeEngine, compose_labels, socket_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PROJECTS = ("shop", "blog", "cache")
HOST = Subject(kind="host", host_id="h")
PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.start", host_id="h", target="shop-web-1")],
    reason="it stopped",
)


class Clock:
    """The wall and the monotonic clock; only Bleep's own sleep moves one without the other."""

    def __init__(self) -> None:
        self.now = T0
        self.mono = 1000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    def suspend(self, seconds: float) -> None:
        """Bleep's machine sleeps: the wall clock runs on, the monotonic clock does not."""
        self.now += timedelta(seconds=seconds)

    def set_back(self, seconds: float) -> None:
        """The wall clock is set back (NTP); the monotonic clock goes on as before."""
        self.now -= timedelta(seconds=seconds)


class FakeRunner:
    def __init__(self, repo: IncidentRepo | None = None) -> None:
        self.started: list[Incident] = []
        self.recovered = 0
        self.ended: list[tuple[str, str]] = []
        self.endings: list[str] = []
        self.back: list[str] = []
        self.repo = repo

    async def start(self, incident: Incident) -> None:
        self.started.append(incident)

    async def recover_on_boot(self) -> list[str]:
        self.recovered += 1
        return []

    async def host_back(self, host_id: str) -> None:
        self.back.append(host_id)

    async def end(
        self, incident_id: str, text: Shown, *, ending: str = "", spare: tuple[str, ...] = ()
    ) -> None:
        if self.repo is not None and await self.repo.close_unfinished(incident_id, spare) is None:
            return
        self.ended.append((incident_id, english(text)))
        self.endings.append(ending)

    async def close(self) -> None:
        return None


def box(name: str, project: str, **fields) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="img",
        state=fields.pop("state", "running"),
        labels=compose_labels(project),
        **fields,
    )


def die(name: str, at: datetime, exit_code: int = 1) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action="die",
        container_id=f"id-{name}",
        container_name=name,
        attributes={"name": name, "exitCode": str(exit_code)},
    )


class Rig:
    def __init__(
        self,
        tmp_path,
        model: FakeChatModel | None = None,
        budgets: Budgets | None = None,
        may_sleep: bool = False,
    ) -> None:
        self.clock = Clock()
        self.engine = FakeEngine(
            "h",
            [box(f"{p}-web-1", p, restart_policy="no") for p in PROJECTS],
            clock=self.clock,
            logs={"shop-web-1": "OOM killed\n"},
        )
        self.db = Database(tmp_path / "bleep.sqlite")
        self.repo = IncidentRepo(self.db, self.clock)
        self.ledger = CostLedger(self.db, self.clock)
        self.budgets = budgets or Budgets()
        self.language: Language = "en"
        self.bus = EventBus(self.db, self.clock)
        self.runner = FakeRunner(self.repo)
        self.quiet = QuietStore(self.db, self.clock)
        self.web_up = True
        self.not_found: set[str] = set()  # projects whose URL answers 404 while the host is up
        self.roles: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if not self.web_up:
                raise httpx.ConnectError("no route to host", request=request)
            if request.url.host.split(".")[0] in self.not_found:
                return httpx.Response(404, text="no such page")
            return httpx.Response(200, text="ok")

        async def connect(_host):
            return None, self.engine

        urls = {
            f"compose:{p}": ServiceOverrides(urls=[UrlCheckConfig(url=f"https://{p}.example/")])
            for p in PROJECTS
        }
        host = socket_host(id="h", services=urls, may_sleep=may_sleep)
        self.registry = Registry([host], connect, clock=self.clock)
        self.detector = Detector(Thresholds(), self.clock)
        self.store = UrlCheckStore(self.db)
        self.health = HealthStore(self.db)

        def models(role: str):
            self.roles.append(role)
            return model

        self.manager = WatcherManager(
            self.registry,
            self.detector,
            self.repo,
            self.runner,  # type: ignore[arg-type]
            self.bus,
            clock=self.clock,
            monotonic=self.clock.monotonic,
            models=models if model is not None else None,
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            store=self.store,
            ledger=self.ledger,
            budgets=lambda: self.budgets,
            language=lambda: self.language,
            quiet=self.quiet,
            health=self.health,
        )
        self.quiet.subscribe(self.manager.quiet_changed)

    async def boot(self) -> Rig:
        await self.db.migrate()
        await self.registry.start()
        return self

    @property
    def parts(self):
        return self.manager.hosts["h"]

    async def pulse(self, seconds: float = 15, urls: bool = True) -> None:
        """Advance the clock, run one watcher tick and (by default) one round of URL checks."""
        self.clock.advance(seconds)
        await self.parts.watcher.tick()
        if urls:
            await self.parts.urls.tick()

    async def incidents(self):
        return await self.repo.list()

    async def lose_host(self) -> None:
        self.engine.drop()
        self.web_up = False

    async def kill(self, project: str) -> None:
        index = PROJECTS.index(project)
        name = f"{project}-web-1"
        self.engine.history.append(die(name, self.clock.now + timedelta(seconds=1)))
        self.engine.container_list[index] = box(
            name, project, state="exited", exit_code=1, restart_policy="no"
        )
        for _ in range(6):  # past the grace window
            await self.pulse(urls=False)

    async def regain_host(self) -> None:
        self.engine.restore()
        self.web_up = True


async def test_a_host_down_for_five_minutes_opens_one_incident_and_the_urls_attach_to_it(
    tmp_path, caplog
):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(25):  # 6 min 15 s of failed ticks and failing URL checks
        await r.pulse()
    [incident] = await r.incidents()
    assert incident.subject == HOST
    assert [i.id for i in r.runner.started] == [incident.id]
    signals = await r.repo.signals(incident.id)
    assert any(s.kind == "host_unreachable" for s in signals)
    attached = {s.subject.service_id for s in signals if s.kind == "url_failed"}
    assert attached == {f"h/compose:{p}" for p in PROJECTS}  # all three services' failures
    notices = [e for e in await r.bus.history(incident.id) if e.kind == "notice"]
    assert notices and "unreachable" in notices[0].payload["text"]
    assert not [rec for rec in caplog.records if rec.levelname == "ERROR"]
    recorded = await r.store.latest("h")  # every check of the tick reached the dashboard row
    assert sorted(check.url for _, check, _ in recorded) == [
        f"https://{p}.example/" for p in sorted(PROJECTS)
    ]


def sleep_inside_next_reconnects(r: Rig, *seconds: float) -> None:
    """Bleep's machine sleeps in the middle of the next ticks' connects, one sleep each: the
    tick began before the sleep and ends after it."""
    reconnect = r.registry.reconnect
    sleeps = list(seconds)

    async def sleepy(host_id: str) -> bool:
        if sleeps:
            r.clock.suspend(sleeps.pop(0))
        return await reconnect(host_id)

    r.registry.reconnect = sleepy  # type: ignore[method-assign]


async def test_bleeps_own_sleep_between_ticks_opens_no_incident_inside_the_grace_window(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(4):  # a minute of failed ticks before Bleep's machine sleeps
        await r.pulse()
    r.clock.suspend(8 * 3600)  # it slept all night; on waking the network is not back yet
    for _ in range(4):  # the grace window: four ticks after the wake
        await r.pulse()
    assert await r.incidents() == []

    # Past the grace window an outage counts again, from zero: five minutes of failed ticks.
    for _ in range(20):
        await r.pulse()
    assert await r.incidents() == []
    await r.pulse()
    [incident] = await r.incidents()
    assert incident.subject == HOST


async def test_a_sleep_that_starts_inside_a_tick_is_noticed_before_its_probe_counts(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(4):  # a minute of failed ticks: the outage timer runs
        await r.pulse()
    sleep_inside_next_reconnects(r, 8 * 3600)  # the lid closes inside the next connect
    await r.pulse()
    assert await r.incidents() == []
    assert r.manager.waking()


async def test_a_second_dark_wake_after_a_sleep_inside_a_connect_opens_nothing(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    r.clock.suspend(15 * 60)  # dark wake 1, noticed between ticks
    for _ in range(8):  # it lasts two minutes, failing
        await r.pulse()
    sleep_inside_next_reconnects(r, 15 * 60)  # the next sleep begins inside a connect
    for _ in range(2):  # dark wake 2
        await r.pulse()
    assert await r.incidents() == []


async def test_short_dark_wakes_whose_sleeps_start_inside_connects_open_nothing(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for wake in range(20):  # every 15 minutes, a wake of two failed ticks
        if wake % 2:
            sleep_inside_next_reconnects(r, 15 * 60)
        else:
            r.clock.suspend(15 * 60)
        await r.pulse()
        await r.pulse()
    assert await r.incidents() == []
    assert r.runner.started == []


async def test_a_wall_clock_set_back_is_no_wake_and_never_stretches_the_grace(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.clock.suspend(3600)  # a real wake
    await r.pulse()
    assert r.manager.waking()
    r.clock.set_back(3600)  # then NTP steps the wall clock back an hour
    await r.lose_host()
    for _ in range(26):  # six and a half minutes of a host that does not answer
        await r.pulse()
    [incident] = await r.incidents()
    assert incident.subject == HOST


async def test_a_url_check_taken_across_bleeps_sleep_is_not_counted(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.web_up = False  # the URLs fail; the host itself still answers
    await r.parts.urls.tick()  # one failure: one more opens a service incident
    r.clock.suspend(3600)
    await r.parts.urls.tick()  # the network is not back after the wake yet
    r.clock.advance(30)
    await r.parts.urls.tick()  # nor half a minute later, still inside the grace window
    assert await r.incidents() == []


async def test_a_slow_failing_tick_is_not_taken_for_bleeps_own_sleep(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    reconnect = r.registry.reconnect

    async def slow_reconnect(host_id: str) -> bool:
        r.clock.advance(60)  # each failed tick takes the full minute its bound allows
        return await reconnect(host_id)

    r.registry.reconnect = slow_reconnect  # type: ignore[method-assign]
    for _ in range(5):
        await r.pulse(urls=False)  # the usual 15 s sleep, then the slow tick
    [incident] = await r.incidents()  # 5 minutes and more of outage: it opens as before
    assert incident.subject == HOST


async def test_a_host_that_may_sleep_opens_nothing_when_it_stops_answering(tmp_path):
    r = await Rig(tmp_path, may_sleep=True).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(40):  # ten minutes asleep, every URL failing with it
        await r.pulse()
    assert await r.incidents() == [] and r.runner.started == []
    await r.regain_host()
    for _ in range(4):
        await r.pulse()
    assert await r.incidents() == []  # the URL failures held while it slept were dropped


async def test_every_tick_that_reaches_a_host_tells_the_runner(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.pulse()
    assert r.runner.back == ["h", "h"]  # level, not edge: a run parked late is caught next tick
    await r.lose_host()
    for _ in range(3):
        await r.pulse()
    assert r.runner.back == ["h", "h"]  # never while it does not answer
    await r.regain_host()
    await r.pulse()
    assert r.runner.back == ["h", "h", "h"]


async def test_an_asleep_hosts_services_are_not_stored_as_down(tmp_path):
    r = await Rig(tmp_path, may_sleep=True).boot()
    await r.pulse()
    shop = "h/compose:shop"
    before = await r.health.current(shop)
    await r.lose_host()
    for _ in range(8):  # two minutes asleep, every URL failing
        await r.pulse()
    assert await r.health.current(shop) == before  # still "ok since", never "down"


async def finish(r: Rig, exit_code: int) -> None:
    """shop's container, a job under `on-failure`, exits; then the grace window passes."""
    name = "shop-web-1"
    r.engine.history.append(die(name, r.clock.now + timedelta(seconds=1), exit_code))
    r.engine.container_list[0] = box(
        name, "shop", state="exited", exit_code=exit_code, restart_policy="on-failure"
    )
    for _ in range(6):
        await r.pulse(urls=False)


@pytest.mark.parametrize(
    ("exits", "exit_code", "opens"), [([], 0, True), (["*"], 0, False), (["*"], 3, True)]
)
async def test_a_job_named_in_exits_opens_nothing_on_exit_0_and_a_crash_still_does(
    tmp_path, exits, exit_code, opens
):
    r = await Rig(tmp_path).boot()
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="on-failure")
    r.registry.override("h", [], {"compose:shop": ServiceOverrides(exits=exits)})
    await r.pulse(urls=False)
    await finish(r, exit_code)
    assert bool(await r.incidents()) is opens


async def test_held_url_failures_are_announced_once_per_new_set_of_urls(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(25 + 40):  # the host incident opens, then ten more minutes of failing checks
        await r.pulse()
    [incident] = await r.incidents()
    failing = [
        e.payload["text"]
        for e in await r.bus.history(incident.id)
        if e.kind == "notice" and "URL checks failing" in e.payload["text"]
    ]
    assert len(failing) == 1  # not one per URL check, which would flood the report
    assert all(f"https://{p}.example/" in failing[0] for p in PROJECTS)
    attached = [s for s in await r.repo.signals(incident.id) if s.kind == "url_failed"]
    assert len(attached) > 3 * 5  # the failures themselves still attach as they come


async def test_url_failures_before_the_host_incident_wait_for_it_instead_of_opening_their_own(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(10):  # 2.5 min: long enough for every service to fail two checks
        await r.pulse()
    assert await r.incidents() == []
    assert r.runner.started == []
    assert sum(len(v) for v in r.manager._held["h"].values()) >= 3  # kept for the host incident


async def test_a_host_back_within_five_minutes_opens_nothing_and_drops_held_failures(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(6):
        await r.pulse()
    await r.regain_host()
    await r.pulse()
    await r.pulse()
    assert await r.incidents() == []
    assert r.manager._held == {}


async def test_the_watcher_resumes_the_event_stream_when_the_host_comes_back(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(25):
        await r.pulse()
    [host_incident] = await r.incidents()
    await r.regain_host()
    # While the host was away a container died and stayed down.
    r.engine.history.append(die("shop-web-1", T0 + timedelta(seconds=100)))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", state="exited", exit_code=1, restart_policy="no"
    )
    await r.repo.set_status(host_incident.id, "resolved")  # the host incident is handled
    for _ in range(6):  # the gap is replayed once, then the container is past its grace window
        await r.pulse()
    opened = [i for i in await r.incidents() if i.subject.kind == "service"]
    assert [i.subject.service_id for i in opened] == ["h/compose:shop"]
    assert [i.id for i in r.runner.started] == [host_incident.id, opened[0].id]
    assert r.registry.hosts()[0].reachable


async def test_the_runner_starts_only_for_a_newly_opened_incident(tmp_path):
    r = await Rig(tmp_path).boot()

    def disk() -> Signal:
        return Signal(subject=HOST, kind="disk", at=r.clock(), detail={"percent": 95})

    await r.manager.sink(disk())  # opens
    r.clock.advance(31)  # past the debounce window: the detector asks to open again
    await r.manager.sink(disk())
    r.clock.advance(5)
    await r.manager.sink(disk())  # inside the new window: attach
    [incident] = await r.incidents()
    assert [i.id for i in r.runner.started] == [incident.id]
    assert len(await r.repo.signals(incident.id)) == 3


async def test_a_new_incident_after_the_old_one_closed_starts_a_run_again(tmp_path):
    r = await Rig(tmp_path).boot()
    signal = Signal(subject=HOST, kind="disk", at=T0, detail={"percent": 95})
    await r.manager.sink(signal)
    await r.repo.set_status(r.runner.started[0].id, "resolved")
    r.clock.advance(31)
    freed = {"at": r.clock(), "detail": {"percent": 50}}  # the fix held for a reading (#356)
    await r.manager.sink(signal.model_copy(update=freed))
    r.clock.advance(60)
    await r.manager.sink(signal.model_copy(update={"at": r.clock()}))
    assert len(r.runner.started) == 2


async def test_a_self_recovered_container_is_logged_with_a_one_line_explanation(tmp_path):
    model = FakeChatModel(script=[AIMessage("It ran out of memory. Then it restarted.")])
    r = await Rig(tmp_path, model).boot()
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    died = r.clock.now + timedelta(seconds=2)
    r.engine.history.append(die("shop-web-1", died))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", restart_policy="always", started_at=died + timedelta(seconds=3)
    )
    await r.pulse(urls=False)
    await r.manager.drain()
    [recovery] = await r.repo.recoveries()
    assert (recovery.host_id, recovery.service_id, recovery.container) == (
        "h",
        "h/compose:shop",
        "shop-web-1",
    )
    assert recovery.explanation == "It ran out of memory."
    assert r.roles == ["cheap"]
    assert "OOM killed" in str(model.seen[0][0].content)
    assert await r.incidents() == []
    assert r.runner.started == []


async def test_the_recovery_line_is_asked_for_in_the_language_in_force_at_the_time(tmp_path):
    """#374: the manager reads the language at each line, so a change applies to the next."""
    model = FakeChatModel(
        script=[AIMessage("It ran out of memory."), AIMessage("Der Speicher war voll.")]
    )
    r = await Rig(tmp_path, model).boot()
    await recover_shop(r)
    r.language = "de"
    r.clock.advance(60)
    await recover_shop(r)

    rule = prompts.load("watcher/recovery_line_de").strip()
    assert [rule in str(seen[0].content) for seen in model.seen] == [False, True]
    newest, first = await r.repo.recoveries()
    assert (first.explanation, newest.explanation) == (
        "It ran out of memory.",
        "Der Speicher war voll.",
    )


async def recover_shop(r: Rig) -> None:
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    died = r.clock.now + timedelta(seconds=2)
    r.engine.history.append(die("shop-web-1", died))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", restart_policy="always", started_at=died + timedelta(seconds=3)
    )
    await r.pulse(urls=False)
    await r.manager.drain()


def priced_reply(text: str) -> AIMessage:
    return AIMessage(
        text,
        usage_metadata={"input_tokens": 300, "output_tokens": 20, "total_tokens": 320},
        response_metadata={"model_name": "openai/gpt-6-luna", "cost": 0.002},
    )


async def test_the_recovery_lines_model_call_is_in_the_usage_ledger_and_the_daily_spend(tmp_path):
    r = await Rig(tmp_path, FakeChatModel(script=[priced_reply("It ran out of memory.")])).boot()
    await recover_shop(r)
    [row] = await r.ledger.by_incident()
    assert (row.key, row.calls, row.tokens_in, row.tokens_out) == (RECOVERY_USAGE, 1, 300, 20)
    assert await r.ledger.spent_today() == 0.002


async def test_with_the_daily_cap_spent_the_recovery_is_logged_without_a_model_call(tmp_path):
    model = FakeChatModel(script=[priced_reply("It ran out of memory.")])
    r = await Rig(tmp_path, model, Budgets(daily_cost_usd=0.5)).boot()
    await r.ledger.add("inc-1", "openai/gpt-6-luna", 1000, 100, 0.5)
    await recover_shop(r)
    [recovery] = await r.repo.recoveries()
    assert recovery.explanation == CAP_SPENT_EXPLANATION
    assert model.seen == []
    assert [row.key for row in await r.ledger.by_incident()] == ["inc-1"]


async def test_with_the_cap_spent_and_no_logs_the_line_says_there_were_no_logs(tmp_path):
    model = FakeChatModel(script=[])
    r = await Rig(tmp_path, model, Budgets(daily_cost_usd=0.5)).boot()
    r.engine.log_text = {}
    await r.ledger.add("inc-1", "openai/gpt-6-luna", 1000, 100, 0.5)
    await recover_shop(r)
    [recovery] = await r.repo.recoveries()
    assert recovery.explanation == NO_LOGS_LINE
    assert model.seen == []


async def test_a_recovery_is_logged_even_when_the_model_fails(tmp_path):
    r = await Rig(tmp_path, FakeChatModel(script=[])).boot()  # an empty script raises
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    died = r.clock.now + timedelta(seconds=2)
    r.engine.history.append(die("shop-web-1", died))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", restart_policy="always", started_at=died + timedelta(seconds=3)
    )
    await r.pulse(urls=False)
    await r.manager.drain()
    [recovery] = await r.repo.recoveries()
    assert recovery.explanation == NO_EXPLANATION


async def test_start_recovers_unfinished_runs_then_watches_each_host_and_stop_ends_it(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.manager.start()
    assert r.runner.recovered == 1
    tasks = r.manager._tasks["h"]
    assert sorted(t.get_name() for t in tasks) == ["probe-h", "urls-h", "watcher-h"]
    await asyncio.sleep(0.05)  # the first ticks run
    assert r.registry.hosts()[0].reachable
    await r.manager.stop()
    assert all(t.done() for t in tasks)


async def test_a_loop_that_dies_of_an_error_is_started_again(monkeypatch):
    monkeypatch.setattr(manager_module, "RESTART_BACKOFF_S", (0,))
    runs = []

    async def flaky() -> None:
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")
        await asyncio.Event().wait()

    task = asyncio.create_task(WatcherManager._supervised("watcher-h", flaky))
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(runs) == 3


async def test_a_death_on_an_unreachable_host_is_not_judged_by_another_hosts_ticks(tmp_path):
    """A container on host a dies and a stops answering. Host b keeps ticking past the grace
    window; a's last listing (the container exited) is stale, so it must not open an incident
    for a service Bleep cannot see. The death waits for a's own next snapshot."""
    clock = Clock()
    engines = {
        "a": FakeEngine("a", [box("shop-web-1", "shop", restart_policy="no")], clock=clock),
        "b": FakeEngine("b", [box("blog-web-1", "blog", restart_policy="no")], clock=clock),
    }

    async def connect(host):
        return None, engines[host.id]

    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    registry = Registry([socket_host(id="a"), socket_host(id="b")], connect, clock=clock)
    await registry.start()
    repo, runner = IncidentRepo(db, clock), FakeRunner()
    manager = WatcherManager(
        registry,
        Detector(Thresholds(), clock),
        repo,
        runner,  # type: ignore[arg-type]
        EventBus(db, clock),
        clock=clock,
        monotonic=clock.monotonic,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    a, b = manager.hosts["a"].watcher, manager.hosts["b"].watcher

    await a.tick()
    await b.tick()
    clock.advance(10)
    engines["a"].history.append(die("shop-web-1", clock.now))
    engines["a"].container_list[0] = box(
        "shop-web-1", "shop", state="exited", exit_code=137, restart_policy="no"
    )
    clock.advance(5)
    await a.tick()  # the death is seen, and a's listing now says exited
    engines["a"].drop()  # then a stops answering, as it often does under memory pressure
    for _ in range(8):  # two minutes: well past the 60 s grace window
        clock.advance(15)
        assert await a.tick() is False
        await b.tick()

    assert await repo.list() == []
    assert runner.started == []

    engines["a"].restore()  # back, and the container is still down: a's own snapshot judges it
    clock.advance(15)
    assert await a.tick() is True
    [incident] = await repo.list()
    assert incident.subject.service_id == "a/compose:shop"
    assert [i.id for i in runner.started] == [incident.id]


# -- hosts added, changed and removed on the dashboard (#194) -----------------------------------


def dashboard_host(address: str = "box.lan") -> HostConfig:
    return HostConfig(
        id="c", name="C", kind="ssh", source="dashboard", address=address, user="deploy"
    )


class TwoMachines:
    """The connector a dashboard host gets: each address is another machine."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.machines = {
            "box.lan": FakeEngine("c", [box("blog-web-1", "blog")], clock=rig.clock),
            "other.lan": FakeEngine("c", [box("wiki-web-1", "wiki")], clock=rig.clock),
        }
        self.reached: list[str] = []

    async def __call__(self, host):
        if host.id == "h":
            return None, self.rig.engine
        self.reached.append(host.address)
        return None, self.machines[host.address]


async def dashboard_rig(tmp_path) -> tuple[Rig, TwoMachines]:
    r = Rig(tmp_path)
    machines = TwoMachines(r)
    r.registry._connector = machines
    await r.boot()
    await r.manager.start()
    return r, machines


async def test_an_added_host_is_watched_at_once(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))

    assert sorted(t.get_name() for t in r.manager._tasks["c"]) == [
        "probe-c",
        "urls-c",
        "watcher-c",
    ]
    for _ in range(50):
        await asyncio.sleep(0.01)
        if r.registry.services("c"):
            break
    assert [s.name for s in r.registry.services("c")] == ["blog"]
    await r.manager.stop()


async def test_a_new_connection_starts_the_watch_again_without_reading_old_containers_as_dead(
    tmp_path,
):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.stop()  # ticks by hand from here
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    await r.manager.hosts["c"].watcher.tick()
    old_watcher = r.manager.hosts["c"].watcher

    await r.manager.host_changed(HostChange("c", "connection", dashboard_host("other.lan")))
    assert r.manager.hosts["c"].watcher is not old_watcher
    r.clock.advance(15)
    assert await r.manager.hosts["c"].watcher.tick()
    assert machines.reached == ["box.lan", "other.lan"]
    assert [s.name for s in r.registry.services("c")] == ["wiki"]
    assert await r.incidents() == []  # blog-web-1 is not on this machine, and not dead


async def test_a_removed_host_stops_being_watched_and_its_open_incidents_are_ended(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    tasks = list(r.manager._tasks["c"])
    subject = Subject(kind="service", host_id="c", service_id="c/compose:blog")
    incident, _ = await r.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="unhealthy", at=T0, container="blog-web-1")]
    )
    other, _ = await r.repo.open_or_attach(
        HOST, [Signal(subject=HOST, kind="disk", at=T0, detail={"percent": 95})]
    )

    await r.manager.host_changed(HostChange("c", "removed", None))
    assert all(t.done() for t in tasks)
    assert "c" not in r.manager.hosts and "c" not in r.manager._tasks
    assert [h.config.id for h in r.registry.hosts()] == ["h"]
    assert machines.machines["box.lan"].closed
    assert r.runner.ended == [(incident.id, english(REMOVED_NOTICE))]  # host h's incident stays
    assert r.runner.endings == ["removed"]
    assert other.id not in [i for i, _ in r.runner.ended]
    await r.manager.stop()


async def test_removing_a_host_leaves_no_approval_of_its_incidents_pending(tmp_path):
    r, _ = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    subject = Subject(kind="service", host_id="c", service_id="c/compose:blog")
    incident, _ = await r.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="unhealthy", at=T0, container="blog-web-1")]
    )
    approvals = ApprovalStore(r.db, "secret")
    await approvals.request(incident.id, PLAN, [])
    await r.repo.set_status(incident.id, "awaiting_approval")

    await r.manager.host_changed(HostChange("c", "removed", None))
    record = await approvals.current(incident.id)
    assert record.closed and record.decision is None
    assert await approvals.pending() == []
    await r.manager.stop()


async def test_a_new_name_keeps_the_watch_running(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    tasks = list(r.manager._tasks["c"])
    renamed = dashboard_host().model_copy(update={"name": "Box"})
    await r.manager.host_changed(HostChange("c", "renamed", renamed))
    assert r.manager._tasks["c"] == tasks and not any(t.done() for t in tasks)
    assert [h.config.name for h in r.registry.hosts() if h.config.id == "c"] == ["Box"]
    await r.manager.stop()


async def test_a_death_pending_on_the_old_machine_opens_nothing_on_the_new_one(tmp_path):
    """The review's probe (#194): blog-web-1 dies on box.lan, then the address moves to
    other.lan, where there is no blog. Past the grace window nothing may open."""
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.stop()  # ticks by hand from here
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    await r.manager.hosts["c"].watcher.tick()
    old = machines.machines["box.lan"]
    old.history.append(die("blog-web-1", r.clock.now + timedelta(seconds=2)))
    old.container_list[0] = box("blog-web-1", "blog", state="exited", restart_policy="always")
    r.clock.advance(15)
    await r.manager.hosts["c"].watcher.tick()
    assert ("c", "blog-web-1") in r.detector._pending

    moved = dashboard_host("other.lan")
    await r.manager.host_changed(HostChange("c", "connection", moved, moved=True))
    assert not any(key[0] == "c" for key in r.detector._pending)
    for _ in range(8):  # two minutes, well past the 60 s grace
        r.clock.advance(15)
        await r.manager.hosts["c"].watcher.tick()
    assert await r.incidents() == []


async def test_a_moved_host_forgets_its_held_url_failures_and_its_incidents_hear_of_it(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    subject = Subject(kind="service", host_id="c", service_id="c/compose:blog")
    incident, _ = await r.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="unhealthy", at=T0, container="blog-web-1")]
    )
    r.manager._held["c"] = {"c/compose:blog": []}
    r.manager._announced["c"] = (incident.id, {"https://blog.example/"})

    moved = dashboard_host("other.lan")
    await r.manager.host_changed(HostChange("c", "connection", moved, moved=True))
    assert "c" not in r.manager._held and "c" not in r.manager._announced
    notices = [e for e in await r.bus.history(incident.id) if e.kind == "notice"]
    assert [e.payload["text"] for e in notices] == [english(manager_module.MOVED_NOTICE)]
    assert (await r.repo.get(incident.id)).status == "open"  # moved, not removed
    await r.manager.stop()


async def test_a_new_key_for_the_same_machine_keeps_what_is_pending(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.stop()
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    r.detector.feed(
        Signal(
            subject=Subject(kind="service", host_id="c", service_id="c/compose:blog"),
            kind="container_died",
            at=r.clock.now,
            container="blog-web-1",
            detail={"exit_code": 1, "restart_policy": "always"},
        )
    )
    await r.manager.host_changed(HostChange("c", "connection", dashboard_host(), moved=False))
    assert ("c", "blog-web-1") in r.detector._pending


async def test_a_removal_that_fails_halfway_still_drops_the_host(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    subject = Subject(kind="service", host_id="c", service_id="c/compose:blog")
    await r.repo.open_or_attach(
        subject, [Signal(subject=subject, kind="unhealthy", at=T0, container="blog-web-1")]
    )

    async def broken(incident_id, text, ending):
        raise RuntimeError("the database is locked")

    r.runner.end = broken
    with pytest.raises(RuntimeError):
        await r.manager.host_changed(HostChange("c", "removed", None))
    assert [h.config.id for h in r.registry.hosts()] == ["h"]
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))  # can come back
    assert "c" in [h.config.id for h in r.registry.hosts()]
    await r.manager.stop()


SHOP = "h/compose:shop"


async def test_a_quiet_service_opens_no_incident_for_a_death(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    assert await r.incidents() == []
    assert r.runner.started == []


async def test_a_quiet_service_does_not_hide_another_service_on_the_host(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.kill("blog")
    [incident] = await r.incidents()
    assert incident.subject.service_id == "h/compose:blog"


async def test_a_quiet_host_opens_nothing_for_its_services_or_itself(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("host", "h", "snooze", T0 + timedelta(hours=2))
    await r.pulse()
    await r.kill("shop")
    await r.lose_host()
    for _ in range(25):  # five minutes and more of an unreachable host
        await r.pulse()
    assert await r.incidents() == []


async def test_a_quiet_services_url_failures_are_not_held_for_the_host_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse()
    await r.lose_host()
    for _ in range(25):
        await r.pulse()
    [incident] = await r.incidents()
    assert incident.subject == HOST
    signals = await r.repo.signals(incident.id)
    assert SHOP not in {s.subject.service_id for s in signals if s.kind == "url_failed"}
    assert not any(SHOP in held for held in r.manager._held.values())


async def test_a_service_quieted_while_its_failures_are_held_is_not_attached_or_named(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    for _ in range(4):  # URL failures are held, the host incident has not opened yet
        await r.pulse()
    assert await r.incidents() == []
    await r.quiet.set("service", SHOP, "leave_alone", None)
    for _ in range(25):
        await r.pulse()
    [incident] = await r.incidents()
    assert incident.subject == HOST
    signals = await r.repo.signals(incident.id)
    assert SHOP not in {s.subject.service_id for s in signals if s.kind == "url_failed"}
    notices = [e.payload["text"] for e in await r.bus.history(incident.id) if e.kind == "notice"]
    assert not any("shop.example" in text for text in notices)
    assert any("blog.example" in text for text in notices)


async def open_shop_incident(r: Rig) -> Incident:
    await r.pulse(urls=False)
    await r.kill("shop")
    [incident] = await r.incidents()
    return incident


async def test_quieting_a_service_closes_its_open_incident_with_a_notice(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await open_shop_incident(r)
    await r.quiet.set("service", SHOP, "leave_alone", None)
    assert r.runner.ended == [(incident.id, "Closed: you told me to leave shop alone.")]
    assert r.runner.endings == ["quiet"]
    assert (await r.repo.get(incident.id)).status == "closed"


async def test_leave_alone_on_an_incident_waiting_for_a_yes_leaves_no_approval_pending(tmp_path):
    """The plan put to the human is closed with the incident, not left pending (#277)."""
    r = await Rig(tmp_path).boot()
    incident = await open_shop_incident(r)
    approvals = ApprovalStore(r.db, "secret")
    await approvals.request(incident.id, PLAN, [])
    await r.repo.set_status(incident.id, "awaiting_approval")
    await r.quiet.set("service", SHOP, "leave_alone", None)

    record = await approvals.current(incident.id)
    assert record.closed and record.decision is None
    assert await approvals.pending() == []


async def test_snoozing_a_host_closes_every_open_incident_on_it_with_its_name(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await open_shop_incident(r)
    await r.quiet.set("host", "h", "snooze", T0 + timedelta(hours=1))
    [(ended_id, text)] = r.runner.ended
    assert ended_id == incident.id
    assert text == "Closed: you snoozed A; I look at it again when the snooze ends."


async def test_quieting_leaves_an_executing_incident_to_finish(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await open_shop_incident(r)
    await r.repo.set_status(incident.id, "executing")
    await r.quiet.set("service", SHOP, "leave_alone", None)
    assert r.runner.ended == []


async def test_quieting_a_service_leaves_other_services_incidents_alone(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    await r.kill("blog")
    await r.quiet.set("service", SHOP, "leave_alone", None)
    assert r.runner.ended == []


async def test_snoozing_again_does_not_end_a_closed_incident_twice(tmp_path):
    r = await Rig(tmp_path).boot()
    await open_shop_incident(r)
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=4))
    assert len(r.runner.ended) == 1


def test_the_notice_names_the_subject_and_the_mode():
    state = QuietState(scope="service", target_id=SHOP, mode="snooze", until=None, since=T0)
    assert english(quiet_notice("shop", state)) == (
        "Closed: you snoozed shop; I look at it again when the snooze ends."
    )


async def test_a_service_still_down_when_its_snooze_ends_opens_one_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await r.kill("shop")
    assert await r.incidents() == []
    r.clock.advance(3600)
    [ended] = await r.quiet.expire()
    assert ended.target_id == SHOP
    [incident] = await r.incidents()
    assert incident.subject.service_id == SHOP
    signals = await r.repo.signals(incident.id)
    assert [s.kind for s in signals] == ["down_past_grace"]
    assert signals[0].container == "shop-web-1"
    assert len(r.runner.started) == 1
    await r.quiet.expire()
    assert len(await r.incidents()) == 1


async def stop_shop(r: Rig, exit_code: int, on_purpose: bool) -> None:
    """shop-web-1 ends with `exit_code`: a `docker stop` it ignored, or a crash (the OOM killer)."""
    at = r.clock.now + timedelta(seconds=1)
    r.engine.history.append(die("shop-web-1", at, exit_code))
    if on_purpose:
        r.engine.history.append(
            DockerEvent(
                time=at,
                type="container",
                action="stop",
                container_id="id-shop-web-1",
                container_name="shop-web-1",
            )
        )
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", state="exited", exit_code=exit_code, restart_policy="no"
    )
    for _ in range(6):
        await r.pulse(urls=False)


async def test_a_137_stop_on_purpose_opens_nothing_when_its_snooze_ends(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await stop_shop(r, 137, on_purpose=True)
    r.clock.advance(3600)
    await r.quiet.expire()
    assert await r.incidents() == []
    assert r.runner.started == []


async def test_a_137_crash_still_opens_an_incident_when_its_snooze_ends(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await stop_shop(r, 137, on_purpose=False)
    r.clock.advance(3600)
    await r.quiet.expire()
    [incident] = await r.incidents()
    signals = await r.repo.signals(incident.id)
    assert [(s.kind, s.container) for s in signals] == [("down_past_grace", "shop-web-1")]


async def test_watch_again_on_a_healthy_service_opens_nothing(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse()
    await r.quiet.clear("service", SHOP)
    assert await r.incidents() == []
    assert r.runner.started == []


async def test_ending_a_hosts_quiet_leaves_a_service_quiet_on_its_own(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("host", "h", "leave_alone", None)
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.quiet.clear("host", "h")
    assert await r.incidents() == []


async def test_ending_a_hosts_quiet_while_it_is_unreachable_opens_the_host_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("host", "h", "snooze", T0 + timedelta(hours=2))
    await r.pulse()
    await r.lose_host()
    for _ in range(3):
        await r.pulse()
    assert not r.registry.hosts()[0].reachable
    await r.quiet.clear("host", "h")
    [incident] = await r.incidents()
    assert incident.subject == HOST
    signals = await r.repo.signals(incident.id)
    assert [s.kind for s in signals] == ["host_unreachable"]


async def test_a_snooze_that_ended_while_bleep_was_down_is_ended_on_the_first_tick(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await r.kill("shop")
    r.clock.advance(2 * 3600)
    assert await r.incidents() == []
    await r.quiet.expire()
    [incident] = await r.incidents()
    assert incident.subject.service_id == SHOP


async def test_ending_a_hosts_quiet_while_it_is_reachable_rechecks_its_services(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("host", "h", "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    assert await r.incidents() == []
    await r.quiet.clear("host", "h")
    [incident] = await r.incidents()
    assert incident.subject.service_id == SHOP
    assert [i.id for i in r.runner.started] == [incident.id]


async def test_a_death_during_a_hosts_quiet_is_judged_when_it_answers_after_the_quiet_ended(
    tmp_path,
):
    """shop dies while the host is left alone, then the host stops answering and the person
    says "Watch again": the host incident opens, and shop's death, whose one event the quiet
    dropped, is judged by the host's next listing instead of being lost."""
    r = await Rig(tmp_path).boot()
    await r.quiet.set("host", "h", "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.lose_host()
    for _ in range(3):
        await r.pulse()
    assert not r.registry.hosts()[0].reachable
    await r.quiet.clear("host", "h")
    [incident] = await r.incidents()
    assert incident.subject == HOST

    await r.regain_host()
    await r.pulse(urls=False)
    [incident] = await r.incidents()  # shop is routed to the open host_unreachable incident
    signals = await r.repo.signals(incident.id)
    shop = [s for s in signals if s.subject.service_id == SHOP]
    assert [(s.kind, s.container) for s in shop] == [("down_past_grace", "shop-web-1")]
    # Only shop is down: URL failures taken while the host was unreachable judge nothing.
    assert {s.subject.service_id for s in signals if s.subject.kind == "service"} == {SHOP}
    await r.pulse(urls=False)
    assert len(await r.repo.signals(incident.id)) == len(signals)  # judged once


async def test_a_snooze_that_ends_before_the_hosts_first_listing_is_judged_by_that_listing(
    tmp_path,
):
    """A snooze passed while Bleep was down, and the host's first connect at boot failed: the
    service has no listing yet, so its recheck waits for the host's first one."""
    r = Rig(tmp_path)
    await r.db.migrate()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", state="exited", exit_code=1, restart_policy="no"
    )
    r.engine.drop()
    await r.registry.start()
    assert r.registry.service(SHOP) is None
    r.clock.advance(2 * 3600)
    await r.quiet.expire()
    assert await r.incidents() == []

    r.engine.restore()
    await r.pulse(urls=False)
    [incident] = await r.incidents()
    assert incident.subject.service_id == SHOP
    assert [i.id for i in r.runner.started] == [incident.id]
    await r.pulse(urls=False)
    assert len(await r.incidents()) == 1


async def test_a_service_snooze_that_ends_while_its_host_is_unreachable_waits_for_it(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.lose_host()
    for _ in range(2):
        await r.pulse(urls=False)
    assert not r.registry.hosts()[0].reachable
    r.clock.advance(3600)
    await r.quiet.expire()
    assert await r.incidents() == []  # its last listing is stale: nothing is judged by it

    await r.regain_host()
    await r.pulse(urls=False)
    [incident] = await r.incidents()
    assert incident.subject.service_id == SHOP


async def test_a_waiting_recheck_skips_a_service_quiet_again_by_then(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "snooze", T0 + timedelta(hours=1))
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.lose_host()
    for _ in range(2):
        await r.pulse(urls=False)
    r.clock.advance(3600)
    await r.quiet.expire()
    await r.quiet.set("service", SHOP, "leave_alone", None)

    await r.regain_host()
    await r.pulse(urls=False)
    assert await r.incidents() == []


async def test_a_removed_host_forgets_its_waiting_rechecks_and_quiet_states(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.stop()
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    await r.quiet.set("service", "c/compose:wiki", "snooze", T0 + timedelta(hours=1))
    r.clock.advance(3600)
    await r.quiet.expire()  # c has no listing yet: wiki's recheck waits for one
    assert "c" in r.manager._deferred
    await r.quiet.set("host", "c", "leave_alone", None)
    await r.quiet.set("service", "c/compose:blog", "leave_alone", None)
    await r.quiet.set("service", SHOP, "leave_alone", None)
    heard = []

    async def listener(change):
        heard.append(change)

    r.quiet.subscribe(listener)
    await r.manager.host_changed(HostChange("c", "removed", None))
    assert "c" not in r.manager._deferred
    assert [(q.scope, q.target_id) for q in await r.quiet.active()] == [("service", SHOP)]
    assert heard == []  # a removed host is not judged again


async def test_one_failing_end_does_not_keep_a_quiet_host_from_closing_the_others(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    await r.kill("shop")
    await r.kill("blog")
    first, second = sorted(await r.incidents(), key=lambda i: i.opened_at)
    original = r.runner.end

    async def end(
        incident_id: str, text: Shown, *, ending: str = "", spare: tuple[str, ...] = ()
    ) -> None:
        if incident_id == first.id:
            raise RuntimeError("the database is locked")
        await original(incident_id, text, ending=ending, spare=spare)

    r.runner.end = end
    await r.quiet.set("host", "h", "leave_alone", None)
    assert [i for i, _ in r.runner.ended] == [second.id]


# -- health since (#232) ---------------------------------------------------------------------


async def test_each_services_health_is_stored_from_the_first_tick_that_judged_it(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    first = r.clock()
    for _ in range(3):
        await r.pulse()
    for project in PROJECTS:
        assert await r.health.current(f"h/compose:{project}") == ("ok", first)


async def test_a_failing_url_round_moves_since_to_when_the_service_went_down(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.web_up = False  # the host still answers; its URLs do not
    await r.pulse()
    went_down = r.clock()
    await r.pulse()
    assert await r.health.current(SHOP) == ("down", went_down)
    r.web_up = True
    await r.pulse()
    assert await r.health.current(SHOP) == ("ok", r.clock())


async def test_a_dead_container_moves_since_on_the_poll_tick(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.kill("shop")  # polls only, no URL rounds
    current = await r.health.current(SHOP)
    assert current is not None and current[0] == "down"
    assert await r.health.current("h/compose:blog") == ("ok", T0 + timedelta(seconds=15))


async def test_a_quiet_services_health_is_still_stored(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse(urls=False)
    await r.kill("shop")
    current = await r.health.current(SHOP)
    assert current is not None and current[0] == "down"
    assert await r.incidents() == []


async def test_a_removed_or_moved_host_forgets_its_services_health(tmp_path):
    r, machines = await dashboard_rig(tmp_path)
    await r.manager.stop()  # ticks by hand from here
    await r.manager.host_changed(HostChange("c", "added", dashboard_host()))
    await r.manager.hosts["c"].watcher.tick()
    assert await r.health.current("c/compose:blog") is not None

    moved = dashboard_host("other.lan")
    await r.manager.host_changed(HostChange("c", "connection", moved, moved=True))
    assert await r.health.current("c/compose:blog") is None
    await r.manager.hosts["c"].watcher.tick()
    assert await r.health.current("c/compose:wiki") is not None

    await r.manager.host_changed(HostChange("c", "removed", None))
    assert await r.health.current("c/compose:wiki") is None


async def test_a_host_whose_events_fail_while_its_listing_works_has_not_answered(tmp_path):
    """A hung engine behind a working session: the registry still calls the host reachable, but
    no probe reached it, so its host-unreachable incident waits and is announced."""
    r = await Rig(tmp_path).boot()
    await r.pulse()
    assert r.manager.answered("h") == r.clock.now

    async def failing(since, until):
        raise EngineError("h", "events timed out")

    r.engine.events = failing  # type: ignore[method-assign]
    for _ in range(3):
        await r.pulse(urls=False)
    assert r.registry.hosts()[0].reachable  # what the quiet close used to trust
    assert r.manager.answered("h") is None


# --- a failure that outlives its incident (#356) ---


async def close_open(r: Rig, subject: Subject) -> Incident:
    """End the subject's open incident as a run that proposed no action does."""
    incident_id = await r.repo.open_incident_id(subject)
    assert incident_id is not None
    await r.repo.set_status(incident_id, "closed")
    incident = await r.repo.get(incident_id)
    assert incident is not None
    return incident


async def minutes(r: Rig, n: int) -> None:
    """A watcher tick and a URL round once a minute, as the live backend ran on 2026-11-09."""
    for _ in range(n):
        await r.pulse(60)


async def test_a_url_failure_that_outlives_its_incident_opens_again_only_after_a_growing_hold(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    await r.pulse()  # the URL answers: Bleep has seen it pass
    r.not_found.add("shop")
    await minutes(r, 2)
    assert len(r.runner.started) == 1
    first = await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))

    await minutes(r, 59)  # it was one new incident a minute
    assert [i.id for i in await r.incidents()] == [first.id]
    await minutes(r, 2)  # an hour after the close
    assert len(r.runner.started) == 2
    second = r.runner.started[-1]
    assert "seen 2 times" in second.title  # the one message about it says it is a repeat

    await close_open(r, second.subject)
    await minutes(r, 118)  # the second hold is two hours
    assert len(r.runner.started) == 2
    await minutes(r, 3)
    assert len(r.runner.started) == 3
    assert {i.subject.service_id for i in r.runner.started} == {SHOP}


async def test_a_url_that_passed_once_since_its_incident_opens_again_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await minutes(r, 2)
    await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    r.not_found.clear()
    await minutes(r, 1)  # one passing check: it recovered
    r.not_found.add("shop")
    await minutes(r, 2)
    assert len(r.runner.started) == 2
    assert r.runner.started[-1].subject.service_id == SHOP


async def test_an_open_incident_still_takes_the_failures_that_go_on(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await minutes(r, 6)
    [incident] = await r.incidents()
    failures = [s for s in await r.repo.signals(incident.id) if s.kind == "url_failed"]
    assert len(failures) == 6
    assert len(r.runner.started) == 1


async def test_a_failure_bleep_never_saw_pass_since_its_restart_waits_for_the_last_hold(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await minutes(r, 2)
    await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    r.manager.detector = Detector(Thresholds(), r.clock)  # the backend restarted
    await minutes(r, 59)
    assert len(r.runner.started) == 1
    await minutes(r, 2)
    assert len(r.runner.started) == 2


async def test_a_host_still_unreachable_after_its_incident_ended_waits_for_the_hold(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    await minutes(r, 6)
    assert [i.subject for i in r.runner.started] == [HOST]
    closed = await close_open(r, HOST)  # closed by the person while the host is still down
    await minutes(r, 59)
    assert [i.id for i in await r.incidents()] == [closed.id]
    await minutes(r, 6)  # the hold is over, and five more minutes down open the repeat
    assert [i.subject for i in r.runner.started] == [HOST, HOST]


def test_the_hold_doubles_from_an_hour_up_to_a_day():
    hours = [repeat_hold(n) / timedelta(hours=1) for n in (1, 2, 3, 4, 5, 6, 7, 500)]
    assert hours == [1, 2, 4, 8, 16, 24, 24, 24]


# --- one broken service, one run: a reading held by an incident of another kind (#366) ---


def turn_unhealthy(r: Rig, project: str) -> None:
    """The container's healthcheck runs out of retries, and the proxy, which routes only to
    healthy containers, answers its URL with a 404 from then on (Coolify on 2026-11-09)."""
    name = f"{project}-web-1"
    r.engine.history.append(
        DockerEvent(
            time=r.clock.now + timedelta(seconds=1),
            type="container",
            action="health_status: unhealthy",
            container_id=f"id-{name}",
            container_name=name,
            attributes={"name": name},
        )
    )
    r.not_found.add(project)


async def unhealthy_incident_ended(r: Rig, project: str) -> Incident:
    await r.pulse()  # every URL answers: Bleep has seen them pass
    turn_unhealthy(r, project)
    await r.pulse(urls=False)
    [opened] = r.runner.started
    assert opened.title.startswith("unhealthy on")
    return await close_open(r, opened.subject)


async def test_a_url_failure_after_its_services_unhealthy_incident_ended_waits_for_its_hold(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    first = await unhealthy_incident_ended(r, "shop")

    await minutes(r, 59)  # the URL fails every check, for the same reason
    assert [i.id for i in await r.incidents()] == [first.id]
    await minutes(r, 2)  # an hour after the close
    assert len(r.runner.started) == 2
    second = r.runner.started[-1]
    assert second.title.startswith("url failed on")
    assert second.fold_of == first.id  # a repeat of the failure: no second "opened" message
    assert "seen 2 times" in second.title

    await close_open(r, second.subject)
    await minutes(r, 118)  # two incidents of it ended: the next hold is two hours
    assert len(r.runner.started) == 2
    await minutes(r, 3)
    assert len(r.runner.started) == 3


async def test_a_url_failure_attached_to_the_unhealthy_incident_is_held_after_it_too(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    turn_unhealthy(r, "shop")
    await r.pulse(urls=False)
    await minutes(r, 2)  # the URL fails while the incident is open: its failures attach to it
    first = await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    assert any(s.kind == "url_failed" for s in await r.repo.signals(first.id))
    await minutes(r, 59)
    assert [i.id for i in await r.incidents()] == [first.id]


async def test_another_service_on_the_host_is_not_held_by_its_neighbours_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await unhealthy_incident_ended(r, "shop")
    r.not_found.add("blog")
    await minutes(r, 2)
    [_, blog] = r.runner.started
    assert blog.subject.service_id == "h/compose:blog"
    assert blog.fold_of is None


async def test_a_url_that_passed_after_the_unhealthy_incident_is_a_new_failure(tmp_path):
    r = await Rig(tmp_path).boot()
    await unhealthy_incident_ended(r, "shop")
    r.not_found.clear()
    await minutes(r, 1)  # one passing check: the service recovered
    r.not_found.add("shop")
    await minutes(r, 2)
    [_, again] = r.runner.started
    assert again.title.startswith("url failed on")
    assert again.fold_of is None


async def test_a_hosts_reading_of_another_kind_is_not_held_by_its_ended_incident(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.manager.sink(Signal(subject=HOST, kind="disk", at=r.clock(), detail={"percent": 95}))
    await close_open(r, HOST)
    for _ in range(3):  # a full disk says nothing about memory: that is a failure of its own
        r.clock.advance(60)
        low = {"available_percent": 2}
        await r.manager.sink(Signal(subject=HOST, kind="memory", at=r.clock(), detail=low))
    [_, memory] = r.runner.started
    assert memory.title.startswith("memory on")


# --- a restart loop that outlives its incident (#361) ---


def crash(r: Rig, name: str = "shop-web-1", back: bool = False) -> None:
    """The container dies under `always`: running again two seconds later when `back`, else
    Docker is still waiting to restart it, as the demo shop's bad env was on 2026-11-09."""
    died = r.clock.now + timedelta(seconds=1)
    index = next(i for i, c in enumerate(r.engine.container_list) if c.name == name)
    container_id = r.engine.container_list[index].id
    r.engine.history.append(die(name, died).model_copy(update={"container_id": container_id}))
    fields = {"started_at": died + timedelta(seconds=2)} if back else {"state": "restarting"}
    again = box(name, "shop", restart_policy="always", **fields)
    r.engine.container_list[index] = again.model_copy(update={"id": container_id})


async def crashing(r: Rig, deaths: int, every_min: int = 1, **how) -> None:
    for _ in range(deaths):
        crash(r, **how)
        await minutes(r, every_min)


async def kinds_of(r: Rig, incident: Incident) -> set[str]:
    return {s.kind for s in await r.repo.signals(incident.id)}


async def loop_incident_ended(r: Rig) -> Incident:
    """shop-web-1 dies three times in three minutes, back each time: a restart loop, which ends
    with no action while the container goes on crashing."""
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    await crashing(r, 3, back=True)
    [loop] = r.runner.started
    assert "restart_loop" in await kinds_of(r, loop)
    return await close_open(r, loop.subject)


def opened_kinds(r: Rig) -> list[str]:
    """The kind of every open the detector asks the manager for, held or not."""
    kinds: list[str] = []
    handle = r.manager._handle

    async def spy(outcomes: list) -> None:
        opens = [o for o in outcomes if isinstance(o, OpenIncident | AttachSignals)]
        kinds.extend(o.signals[-1].kind for o in opens)
        await handle(outcomes)

    r.manager._handle = spy  # type: ignore[method-assign]
    return kinds


async def test_a_container_still_crash_looping_after_its_loop_incident_waits_for_the_hold(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    first = await loop_incident_ended(r)
    kinds = opened_kinds(r)

    # A death every two minutes for 58 minutes: each is judged down past grace (the repeat came
    # 58 s after the close on 2026-11-09), and every third adds up to a new restart loop.
    await crashing(r, 29, every_min=2)
    assert {"down_past_grace", "restart_loop"} <= set(kinds)
    assert [i.id for i in await r.incidents()] == [first.id]

    await crashing(r, 3, every_min=2)  # an hour after the close
    assert len(r.runner.started) == 2
    second = r.runner.started[-1]
    assert second.fold_of == first.id
    assert "seen 2 times" in second.title

    await close_open(r, second.subject)
    await crashing(r, 58, every_min=2)  # two incidents of it ended: the next hold is two hours
    assert len(r.runner.started) == 2
    await crashing(r, 4, every_min=2)
    assert len(r.runner.started) == 3


async def test_a_loop_of_another_container_of_the_service_opens_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    r.engine.container_list.append(box("shop-web-2", "shop", restart_policy="always"))
    first = await loop_incident_ended(r)
    await crashing(r, 3, name="shop-web-2", back=True)
    [_, other] = r.runner.started
    assert other.subject == first.subject
    assert {s.container for s in await r.repo.signals(other.id)} == {"shop-web-2"}


async def test_a_loop_after_the_container_ran_a_window_without_a_death_opens_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    first = await loop_incident_ended(r)
    await minutes(r, 11)  # it runs for longer than the window (600 s) without a death
    await crashing(r, 3, back=True)
    [_, again] = r.runner.started
    assert "restart_loop" in await kinds_of(r, again)
    assert again.fold_of == first.id  # a repeat of the same loop: one message, with the count


async def test_a_relapse_after_a_verified_fix_opens_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    await crashing(r, 3, back=True)
    [loop] = r.runner.started
    await r.repo.set_status(loop.id, "resolved")  # the fix ran and Verify saw it healthy
    await minutes(r, 4)  # less than a window after the last crash before the fix
    crash(r)
    await minutes(r, 2)
    [_, relapse] = r.runner.started
    assert "down_past_grace" in await kinds_of(r, relapse)


async def test_a_looping_services_url_that_answers_between_crashes_waits_with_the_loop(tmp_path):
    """The container runs a minute and crashes, Docker waits three to restart it, and its URL
    answers while it runs and fails while it is down."""
    r = await Rig(tmp_path).boot()
    first = await loop_incident_ended(r)
    for _ in range(6):
        crash(r)
        r.not_found.add("shop")
        await minutes(r, 3)
        up = box("shop-web-1", "shop", restart_policy="always", started_at=r.clock.now)
        r.engine.container_list[0] = up
        r.not_found.discard("shop")
        await minutes(r, 1)
    assert [i.id for i in r.runner.started] == [first.id]  # one broken service, one run

    await minutes(r, 11)  # it runs a whole window without a crash: the loop is over
    r.not_found.add("shop")
    await minutes(r, 2)
    [_, url] = r.runner.started
    assert url.title.startswith("url failed on")


async def test_a_looping_container_that_turns_unhealthy_before_each_crash_waits_too(tmp_path):
    r = await Rig(tmp_path).boot()
    first = await loop_incident_ended(r)
    for _ in range(3):
        crash(r)
        r.engine.history.append(
            DockerEvent(
                time=r.clock.now + timedelta(seconds=30),
                type="container",
                action="health_status: unhealthy",
                container_id="id-shop-web-1",
                container_name="shop-web-1",
                attributes={"name": "shop-web-1"},
            )
        )
        await minutes(r, 3)
    assert [i.id for i in r.runner.started] == [first.id]


async def held_loop_asking(r: Rig, model: FakeChatModel) -> int:
    """shop-web-1's loop incident ended and it goes on crashing, back each time: nine deaths
    in 18 minutes, well inside the hour's hold. The model calls asked before the hold."""
    await loop_incident_ended(r)
    await r.manager.drain()
    asked = len(model.seen)
    await crashing(r, 9, every_min=2, back=True)
    await r.manager.drain()
    return asked


def lines(count: int) -> FakeChatModel:
    return FakeChatModel(script=[AIMessage("It ran out of memory.")] * count)


async def test_a_death_inside_a_held_loop_asks_the_model_for_no_recovery_line(tmp_path):
    model = lines(10)
    r = await Rig(tmp_path, model).boot()
    asked = await held_loop_asking(r, model)
    assert asked == 2  # the two deaths before the first loop, which was in no loop yet
    assert len(model.seen) == asked
    recoveries = await r.repo.recoveries()
    held = recoveries[: len(recoveries) - asked]  # newest first
    assert len(held) == 6  # every third death adds up to a restart loop, held as well
    assert {recovery.explanation for recovery in held} == {HELD_LOOP_EXPLANATION}
    assert len(r.runner.started) == 1


async def test_a_death_after_the_held_loop_recovered_gets_its_recovery_line_again(tmp_path):
    model = lines(10)
    r = await Rig(tmp_path, model).boot()
    asked = await held_loop_asking(r, model)
    await minutes(r, 11)  # it runs a whole window without a death: the loop is over
    crash(r, back=True)
    await minutes(r, 1)
    await r.manager.drain()
    assert len(model.seen) == asked + 1
    newest = (await r.repo.recoveries())[0]
    assert newest.explanation == "It ran out of memory."


async def test_another_containers_death_beside_a_held_loop_gets_its_recovery_line(tmp_path):
    model = lines(10)
    r = await Rig(tmp_path, model).boot()
    r.engine.container_list.append(box("shop-web-2", "shop", restart_policy="always"))
    r.engine.log_text["shop-web-2"] = "connection reset\n"
    asked = await held_loop_asking(r, model)
    crash(r, name="shop-web-2", back=True)
    await minutes(r, 1)
    await r.manager.drain()
    assert len(model.seen) == asked + 1
    newest = (await r.repo.recoveries())[0]
    assert (newest.container, newest.explanation) == ("shop-web-2", "It ran out of memory.")


async def test_a_death_while_the_loops_incident_is_open_gets_its_recovery_line(tmp_path):
    model = lines(10)
    r = await Rig(tmp_path, model).boot()
    r.engine.container_list[0] = box("shop-web-1", "shop", restart_policy="always")
    await r.pulse(urls=False)
    await crashing(r, 3, back=True)  # the loop's incident opens and waits for its run
    await r.manager.drain()
    asked = len(model.seen)
    crash(r, back=True)
    await minutes(r, 1)
    await r.manager.drain()
    assert len(model.seen) == asked + 1


async def test_a_redeployed_container_that_crashes_opens_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    await loop_incident_ended(r)
    for action in ("stop", "destroy"):  # the deploy replaces the container: a new id
        event = die("shop-web-1", r.clock.now + timedelta(seconds=1))
        r.engine.history.append(event.model_copy(update={"action": action}))
    new = box("shop-web-1", "shop", restart_policy="always", started_at=r.clock.now)
    r.engine.container_list[0] = new.model_copy(update={"id": "id-shop-web-1-new"})
    await minutes(r, 2)
    crash(r)
    await minutes(r, 2)
    [_, again] = r.runner.started
    deaths = [s for s in await r.repo.signals(again.id) if s.kind == "container_died"]
    assert {s.container_id for s in deaths} == {"id-shop-web-1-new"}


# --- a look on request and the repeat holds (#403) ---


async def look_at_shop(r: Rig) -> Incident:
    looks = Looks(r.repo, r.runner, r.quiet, r.registry, clock=r.clock)  # type: ignore[arg-type]
    look = await looks.look(SHOP, "telegram")
    assert look.outcome == "opened" and look.incident is not None
    return look.incident


async def test_a_url_failure_a_look_took_waits_for_its_hold_after_the_look(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()  # the URL answers: Bleep has seen it pass
    look = await look_at_shop(r)
    r.not_found.add("shop")
    await minutes(r, 2)  # the URL fails while the look is open: its failures attach to it
    assert any(s.kind == "url_failed" for s in await r.repo.signals(look.id))
    await close_open(r, look.subject)

    await minutes(r, 59)  # as after any incident that took the failure: no new run
    assert [i.id for i in await r.incidents()] == [look.id]
    await minutes(r, 2)  # an hour after the look closed
    assert len(r.runner.started) == 2 and r.runner.started[-1].title.startswith("url failed")


async def test_a_bare_look_neither_starts_a_hold_nor_waits_for_one(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await minutes(r, 2)
    first = await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    look = await look_at_shop(r)  # during the hold: the person asked
    await close_open(r, look.subject)  # ended before the watcher fed it anything
    assert [s.kind for s in await r.repo.signals(look.id)] == ["look_requested"]

    await minutes(r, 57)  # the hold runs from the first incident, not from the look
    assert {i.id for i in await r.incidents()} == {first.id, look.id}
    await minutes(r, 4)
    assert len(r.runner.started) == 3


async def test_a_failure_after_a_bare_look_opens_at_once(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()  # the URL last passed before the look opened
    look = await look_at_shop(r)
    await close_open(r, look.subject)

    r.not_found.add("shop")
    await minutes(r, 2)

    assert len(r.runner.started) == 2 and r.runner.started[-1].title.startswith("url failed")


# --- what the watcher waits on before it opens an incident (#358) ---


def stop(name: str, at: datetime) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action="stop",
        container_id=f"id-{name}",
        container_name=name,
        attributes={"name": name},
    )


def crash_shop(r: Rig, exit_code: int = 1, stopped: bool = False) -> None:
    """shop-web-1 dies (stopped on purpose first, when `stopped`) and is listed exited."""
    at = r.clock.now + timedelta(seconds=1)
    if stopped:
        r.engine.history.append(stop("shop-web-1", at))
    r.engine.history.append(die("shop-web-1", at, exit_code))
    r.engine.container_list[0] = box(
        "shop-web-1", "shop", state="exited", exit_code=exit_code, restart_policy="no"
    )


async def test_a_url_failing_once_is_watched_with_its_count_until_its_incident_opens(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await r.pulse()
    [pending] = await r.manager.watching()
    assert (pending.kind, pending.subject.service_id) == ("url", SHOP)
    assert (pending.url, pending.failures, pending.failures_to_open) == (
        "https://shop.example/",
        1,
        2,
    )
    await r.pulse()
    assert len(r.runner.started) == 1
    assert await r.manager.watching() == []


async def test_a_death_inside_its_grace_window_is_watched_until_it_opens(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    crash_shop(r)
    await r.pulse(urls=False)
    [pending] = await r.manager.watching()
    assert (pending.kind, pending.container) == ("container", "shop-web-1")
    for _ in range(5):
        await r.pulse(urls=False)
    assert len(r.runner.started) == 1
    assert await r.manager.watching() == []


async def test_a_host_not_answering_is_watched_inside_its_window(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    await r.lose_host()
    await r.pulse(urls=False)
    await r.pulse(urls=False)
    watched = [p for p in await r.manager.watching() if p.subject == HOST]
    assert [p.kind for p in watched] == ["unreachable"]


async def test_a_host_that_may_sleep_is_never_watched_for_not_answering(tmp_path):
    r = await Rig(tmp_path, may_sleep=True).boot()
    await r.pulse()
    await r.lose_host()
    await r.pulse(urls=False)
    await r.pulse(urls=False)
    assert await r.manager.watching() == []


async def test_a_quiet_service_is_not_watched(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.quiet.set("service", SHOP, "leave_alone", None)
    await r.pulse()
    r.not_found.add("shop")
    await r.pulse()
    assert await r.manager.watching() == []


async def test_a_container_stopped_on_purpose_is_not_watched_though_it_exited_137(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    crash_shop(r, exit_code=137, stopped=True)
    await r.pulse(urls=False)
    assert await r.manager.watching() == []


async def test_a_container_already_dead_when_bleep_started_is_not_watched(tmp_path):
    r = Rig(tmp_path)
    crash_shop(r)
    r.engine.history.clear()  # it died before Bleep was there to see it
    await r.boot()
    for _ in range(3):
        await r.pulse(urls=False)
    assert await r.manager.watching() == []


async def test_a_paused_or_created_container_is_not_watched(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    r.engine.container_list[0] = box("shop-web-1", "shop", state="paused")
    r.engine.container_list[1] = box("blog-web-1", "blog", state="created")
    for _ in range(3):
        await r.pulse(urls=False)
    assert await r.manager.watching() == []


async def test_a_container_still_dead_after_its_incident_ended_is_not_watched(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse(urls=False)
    await r.kill("shop")
    await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    for _ in range(3):
        await r.pulse(urls=False)
    assert await r.manager.watching() == []


async def test_a_url_failure_that_outlived_its_incident_is_not_watched_while_held(tmp_path):
    r = await Rig(tmp_path).boot()
    await r.pulse()
    r.not_found.add("shop")
    await minutes(r, 2)
    await close_open(r, Subject(kind="service", host_id="h", service_id=SHOP))
    await minutes(r, 1)  # one failure of the next streak, inside the hour's hold
    assert r.detector.pending() != []
    assert await r.manager.watching() == []
