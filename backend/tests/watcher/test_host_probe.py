from datetime import UTC, datetime

from bleep.hosts.registry import Registry
from bleep.schemas.docker import DiskUsage, HostBasics
from bleep.schemas.settings import Thresholds
from bleep.watcher.detector import Detector, OpenIncident
from bleep.watcher.host_probe import HostProbe
from fakes import FakeEngine, FakeShell, ssh_host

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
GB = 1024**3


async def rig(os: str = "linux"):
    engine = FakeEngine("m1")

    async def connect(_host):
        return FakeShell(os), engine

    registry = Registry([ssh_host(id="m1")], connect)
    await registry.start()
    signals = []

    async def sink(signal):
        signals.append(signal)

    return registry, HostProbe("m1", registry, sink, lambda: T0), signals


def set_basics(registry: Registry, **fields) -> HostBasics:
    basics = HostBasics(os="Linux", **fields)
    registry._mark(registry._entries["m1"], basics=basics)
    return basics


async def test_a_linux_and_a_macos_host_both_yield_disk_memory_and_load():
    for os in ("linux", "darwin"):
        _, probe, signals = await rig(os)
        await probe.tick()
        assert [s.kind for s in signals] == ["disk", "memory", "load"], os
        assert {s.subject.key for s in signals} == {"host:m1:"}
        assert 0 <= signals[1].detail["available_percent"] <= 100


async def test_signals_carry_the_numbers_the_detector_reads():
    registry, probe, signals = await rig()
    set_basics(
        registry,
        disks=[DiskUsage(mount="/", used_percent=40), DiskUsage(mount="/data", used_percent=93.5)],
        mem_total=8 * GB,
        mem_available=2 * GB,
        load15=6.0,
        cpus=4,
    )
    await probe.tick()
    disk, memory, load = signals
    assert disk.detail == {"percent": 93.5, "mount": "/data"}  # the fullest disk
    assert memory.detail["available_percent"] == 25
    assert load.detail["load_per_cpu"] == 1.5


async def test_fields_that_could_not_be_read_make_no_signal():
    registry, probe, signals = await rig()
    set_basics(registry, mem_total=8 * GB, mem_available=GB)  # no disks, no load
    await probe.tick()
    assert [s.kind for s in signals] == ["memory"]


async def test_one_reading_is_fed_once_however_often_the_probe_ticks():
    registry, probe, signals = await rig()
    set_basics(registry, load15=1.0, cpus=2)
    await probe.tick()
    await probe.tick()
    assert len(signals) == 1
    set_basics(registry, load15=1.0, cpus=2)  # the registry read again
    await probe.tick()
    assert len(signals) == 2


async def test_an_unreachable_host_is_not_probed_on_stale_numbers():
    registry, probe, signals = await rig()
    set_basics(registry, load15=1.0, cpus=2)
    registry._mark(registry._entries["m1"], reachable=False)
    assert await probe.tick() == []
    assert signals == []


async def test_a_failing_sink_does_not_drop_the_rest_of_the_reading():
    registry, probe, _ = await rig()
    seen = []

    async def sink(signal):
        seen.append(signal.kind)
        raise RuntimeError("boom")

    probe._sink = sink
    set_basics(registry, mem_total=GB, mem_available=GB, load15=1.0, cpus=1)
    await probe.tick()
    assert seen == ["memory", "load"]


async def test_the_detector_applies_the_thresholds_to_what_the_probe_feeds():
    registry, probe, signals = await rig()
    detector = Detector(Thresholds(), lambda: T0)
    outcomes = []

    async def tick(**fields):
        set_basics(registry, **fields)
        signals.clear()
        await probe.tick()
        for signal in signals:
            outcomes.extend(detector.feed(signal))

    await tick(disks=[DiskUsage(mount="/", used_percent=89.9)])
    assert outcomes == []
    await tick(disks=[DiskUsage(mount="/", used_percent=90)])
    assert [o.subject.kind for o in outcomes if isinstance(o, OpenIncident)] == ["host"]

    outcomes.clear()
    low = {"mem_total": 100, "mem_available": 4}  # 4 % available
    await tick(**low)
    await tick(**low)
    assert outcomes == []  # two polls are not enough
    await tick(**low)
    assert [o.signals[-1].kind for o in outcomes] == ["memory"]

    outcomes.clear()
    busy = {"load15": 2.5, "cpus": 1}
    for _ in range(2):
        await tick(**busy)
    assert outcomes == []
    await tick(**busy)
    assert [o.signals[-1].kind for o in outcomes] == ["load"]
