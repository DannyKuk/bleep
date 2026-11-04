import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from langchain_core.messages import AIMessage

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.bus import EventBus
from bleep.incidents.repository import IncidentRepo
from bleep.llm.usage import CostLedger
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Incident, Signal, Subject
from bleep.schemas.settings import Budgets, Thresholds
from bleep.store.db import Database
from bleep.watcher import manager as manager_module
from bleep.watcher.detector import Detector
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import (
    CAP_SPENT_EXPLANATION,
    NO_EXPLANATION,
    RECOVERY_USAGE,
    WatcherManager,
)
from fakes import FakeChatModel, FakeEngine, socket_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
PROJECTS = ("shop", "blog", "cache")
HOST = Subject(kind="host", host_id="h")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[Incident] = []
        self.recovered = 0

    async def start(self, incident: Incident) -> None:
        self.started.append(incident)

    async def recover_on_boot(self) -> list[str]:
        self.recovered += 1
        return []

    async def close(self) -> None:
        return None


def box(name: str, project: str, **fields) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="img",
        state=fields.pop("state", "running"),
        labels={"com.docker.compose.project": project},
        **fields,
    )


def die(name: str, at: datetime) -> DockerEvent:
    return DockerEvent(
        time=at,
        type="container",
        action="die",
        container_id=f"id-{name}",
        container_name=name,
        attributes={"name": name, "exitCode": "1"},
    )


class Rig:
    def __init__(
        self, tmp_path, model: FakeChatModel | None = None, budgets: Budgets | None = None
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
        self.bus = EventBus(self.db, self.clock)
        self.runner = FakeRunner()
        self.web_up = True
        self.roles: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if not self.web_up:
                raise httpx.ConnectError("no route to host", request=request)
            return httpx.Response(200, text="ok")

        async def connect(_host):
            return None, self.engine

        urls = {
            f"compose:{p}": ServiceOverrides(urls=[UrlCheckConfig(url=f"https://{p}.example/")])
            for p in PROJECTS
        }
        self.registry = Registry([socket_host(id="h", services=urls)], connect, clock=self.clock)
        self.detector = Detector(Thresholds(), self.clock)
        self.store = UrlCheckStore(self.db)

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
            models=models if model is not None else None,
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            store=self.store,
            ledger=self.ledger,
            budgets=lambda: self.budgets,
        )

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
    assert sorted(t.get_name() for t in r.manager._tasks) == ["probe-h", "urls-h", "watcher-h"]
    await asyncio.sleep(0.05)  # the first ticks run
    assert r.registry.hosts()[0].reachable
    tasks = list(r.manager._tasks)
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
