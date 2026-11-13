import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_eval_run import COMPOSE, ITEMS, FakeDockerCli, items_server, no_sleep

from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.tools.default import default_registry
from bleep.tools.registry import ToolContext
from evals.reset_spans import MARGIN, ResetSpans, hide_resets
from evals.run import ShopReset
from fakes import FakeEngine, ssh_host

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def fresh_now():
    """`recent_events` looks back from the real clock, so events are placed from the test's own
    time: a suite that runs long after import cannot push them out of the window (#530)."""
    global NOW
    NOW = datetime.now(UTC)


def at(minutes_ago: float, action: str) -> DockerEvent:
    return DockerEvent(
        time=NOW - timedelta(minutes=minutes_ago),
        type="container",
        action=action,
        container_name="shop-web",
    )


async def read_events(spans: ResetSpans, *events: DockerEvent) -> str:
    """What `recent_events` shows for an engine that holds `events`, behind the harness's filter."""
    engine = FakeEngine(
        "demo",
        [ContainerSummary(id="id-shop-web", name="shop-web", image="shop:1", state="running")],
        events=events,
    )

    async def connect(host):
        return None, engine

    registry = Registry([ssh_host(id="demo", name="Demo")], connector=hide_resets(connect, spans))
    await registry.start()
    tools = default_registry().read_tools(ToolContext("inc-1", "demo", registry, None))
    tool = next(t for t in tools if t.name == "recent_events")
    return await tool.ainvoke({"minutes": 60})


def spans_of(*pairs: tuple[float, float]) -> ResetSpans:
    """Resets that ran from `start` to `end` minutes ago."""
    spans = ResetSpans()
    for start, end in pairs:
        spans.record(NOW - timedelta(minutes=start), NOW - timedelta(minutes=end))
    return spans


async def test_an_event_inside_a_reset_is_hidden_from_the_tool_output():
    out = await read_events(spans_of((10, 8)), at(9, "destroy"), at(9, "create"))
    assert "destroy" not in out and "create" not in out


async def test_an_event_from_the_fault_after_the_reset_is_visible():
    out = await read_events(spans_of((10, 8)), at(9, "destroy"), at(5, "die"))
    assert "die" in out and "destroy" not in out


async def test_an_event_before_the_reset_stays_visible():
    out = await read_events(spans_of((10, 8)), at(30, "oom"), at(9, "start"))
    assert "oom" in out


async def test_every_run_records_its_own_span():
    out = await read_events(
        spans_of((50, 48), (30, 28)),
        at(49, "start"),
        at(40, "die"),  # between the two resets: the first run's own fault
        at(29, "create"),
        at(10, "kill"),
    )
    assert "start" not in out and "create" not in out
    assert "die" in out and "kill" in out


async def test_without_a_reset_nothing_is_filtered():
    out = await read_events(ResetSpans(), at(9, "destroy"), at(5, "die"))
    assert "destroy" in out and "die" in out


async def test_the_margin_is_before_the_start_only():
    spans = spans_of((10, 8))
    start, end = NOW - timedelta(minutes=10), NOW - timedelta(minutes=8)
    assert spans.hides(start - MARGIN + timedelta(milliseconds=1))
    assert spans.hides(start - timedelta(seconds=1.5))
    assert not spans.hides(start - MARGIN - timedelta(seconds=1))
    assert spans.hides(end)
    assert not spans.hides(end + timedelta(seconds=0.5))


async def test_the_live_event_stream_is_filtered_too():
    spans = spans_of((10, 8))
    engine = FakeEngine("demo", [])

    async def connect(_host):
        return None, engine

    _, wrapped = await hide_resets(connect, spans)(ssh_host(id="demo"))
    seen: list[str] = []

    async def drain() -> None:
        async for event in wrapped.stream_events():
            seen.append(event.action)
            if event.action == "die":
                return

    task = asyncio.create_task(drain())
    await asyncio.sleep(0)
    engine.emit(at(9, "destroy"))
    engine.emit(at(5, "die"))
    await asyncio.wait_for(task, 1)
    assert seen == ["die"]


async def test_a_shop_reset_records_its_span_even_when_it_fails():
    spans = ResetSpans()
    client, _ = items_server([])
    before = datetime.now(UTC)
    await ShopReset(COMPOSE, ITEMS, run=FakeDockerCli(), http=client, sleep=no_sleep, spans=spans)()
    assert spans.hides(before)

    failed = ResetSpans()
    reset = ShopReset(COMPOSE, ITEMS, run=FakeDockerCli(up_fails=True), http=client, spans=failed)
    with pytest.raises(Exception, match="port 8080"):
        await reset()
    assert failed.hides(before)
