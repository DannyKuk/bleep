import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.incidents.repository import IncidentRepo, fold_key
from bleep.llm.usage import CostLedger
from bleep.schemas.incidents import RecoveryEvent, Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
async def repo(tmp_path):
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    ticks = iter(range(10_000))
    return IncidentRepo(db, clock=lambda: T0 + timedelta(seconds=next(ticks)))


def svc(service_id="web", host_id="h1") -> Subject:
    return Subject(kind="service", host_id=host_id, service_id=service_id)


def host(host_id="h1") -> Subject:
    return Subject(kind="host", host_id=host_id)


def sig(subject, kind="url_failed") -> Signal:
    return Signal(subject=subject, kind=kind, at=T0, detail={"n": 1})


async def test_first_signals_open_an_incident(repo):
    incident, created = await repo.open_or_attach(svc(), [sig(svc())])
    assert created is True
    assert incident.id.startswith("inc_")
    assert incident.status == "open"
    assert incident.subject == svc()
    assert incident.opened_at == T0
    assert [s.kind for s in await repo.signals(incident.id)] == ["url_failed"]


async def test_second_signal_attaches(repo):
    first, _ = await repo.open_or_attach(svc(), [sig(svc())])
    second, created = await repo.open_or_attach(svc(), [sig(svc(), "unhealthy")])
    assert created is False
    assert second.id == first.id
    assert [s.kind for s in await repo.signals(first.id)] == ["url_failed", "unhealthy"]


async def test_other_subjects_get_their_own_incident(repo):
    a, _ = await repo.open_or_attach(svc("web"), [sig(svc("web"))])
    b, created = await repo.open_or_attach(svc("db"), [sig(svc("db"))])
    assert created and a.id != b.id


async def test_service_signal_attaches_to_unreachable_host_incident(repo):
    host_incident, _ = await repo.open_or_attach(host(), [sig(host(), "host_unreachable")])
    incident, created = await repo.open_or_attach(svc(), [sig(svc())])
    assert created is False
    assert incident.id == host_incident.id
    assert [s.kind for s in await repo.signals(incident.id)] == ["host_unreachable", "url_failed"]


async def test_host_incident_without_unreachable_does_not_absorb(repo):
    await repo.open_or_attach(host(), [sig(host(), "disk")])
    incident, created = await repo.open_or_attach(svc(), [sig(svc())])
    assert created is True
    assert incident.subject == svc()


async def test_unreachable_host_does_not_absorb_other_hosts(repo):
    await repo.open_or_attach(host("h1"), [sig(host("h1"), "host_unreachable")])
    _, created = await repo.open_or_attach(svc(host_id="h2"), [sig(svc(host_id="h2"))])
    assert created is True


@pytest.mark.parametrize("status", ["resolved", "closed", "failed"])
async def test_finished_incident_does_not_absorb(repo, status):
    first, _ = await repo.open_or_attach(svc(), [sig(svc())])
    await repo.set_status(first.id, status)
    second, created = await repo.open_or_attach(svc(), [sig(svc())])
    assert created is True and second.id != first.id


@pytest.mark.parametrize("status", ["investigating", "awaiting_approval", "executing", "verifying"])
async def test_in_progress_incident_still_absorbs(repo, status):
    first, _ = await repo.open_or_attach(svc(), [sig(svc())])
    await repo.set_status(first.id, status)
    second, created = await repo.open_or_attach(svc(), [sig(svc())])
    assert created is False and second.id == first.id


async def test_concurrent_calls_open_one_incident(repo):
    results = await asyncio.gather(
        repo.open_or_attach(svc(), [sig(svc())]),
        repo.open_or_attach(svc(), [sig(svc(), "oom")]),
    )
    assert {r[0].id for r in results} == {results[0][0].id}
    assert sorted(r[1] for r in results) == [False, True]
    assert len(await repo.signals(results[0][0].id)) == 2
    assert len(await repo.list()) == 1


async def test_database_refuses_a_second_open_incident(repo):
    await repo.open_or_attach(svc(), [sig(svc())])
    import aiosqlite

    conn = await repo.db.connect()
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status,"
                " title, opened_at)"
                " VALUES ('x','service','h1','web','service:h1:web','open','t','t')"
            )
    finally:
        await conn.close()


async def test_set_status_closes_and_get_list(repo):
    a, _ = await repo.open_or_attach(svc("a"), [sig(svc("a"))])
    b, _ = await repo.open_or_attach(svc("b"), [sig(svc("b"))])
    await repo.set_status(a.id, "resolved")
    done = await repo.get(a.id)
    assert done.status == "resolved" and done.closed_at is not None
    assert (await repo.get(b.id)).closed_at is None
    assert await repo.get("inc_missing") is None
    assert [i.id for i in await repo.list(status="resolved")] == [a.id]
    newest_first = await repo.list()
    assert [i.id for i in newest_first] == [b.id, a.id]
    assert [i.id for i in await repo.list(limit=1, offset=1)] == [a.id]


async def test_ids_sort_by_creation(repo):
    ids = []
    for n in range(5):
        incident, _ = await repo.open_or_attach(svc(f"s{n}"), [sig(svc(f"s{n}"))])
        ids.append(incident.id)
    assert ids == sorted(ids)


async def test_log_recovery(repo):
    event = RecoveryEvent(
        host_id="h1", service_id="web", container="web-1", at=T0, explanation="OOM, restarted"
    )
    saved = await repo.log_recovery(event)
    assert saved.id is not None
    assert await repo.recoveries() == [saved]


async def test_the_overrides_an_incident_started_with_are_recorded_once(repo):
    incident, _ = await repo.open_or_attach(svc(), [sig(svc())])
    assert incident.prompt_overrides is None

    await repo.record_prompt_overrides(incident.id, {"graph/propose": "a1b2c3"})
    await repo.record_prompt_overrides(incident.id, {"graph/propose": "d4e5f6"})
    assert (await repo.get(incident.id)).prompt_overrides == {"graph/propose": "a1b2c3"}


async def test_an_incident_started_without_overrides_records_none_in_force(repo):
    incident, _ = await repo.open_or_attach(svc(), [sig(svc())])
    await repo.record_prompt_overrides(incident.id, {})
    await repo.record_prompt_overrides(incident.id, {"graph/propose": "d4e5f6"})
    assert (await repo.get(incident.id)).prompt_overrides == {}


async def test_get_many_reads_the_incidents_named_and_skips_unknown_ids(repo):
    web, _ = await repo.open_or_attach(svc("web"), [sig(svc("web"))])
    db, _ = await repo.open_or_attach(svc("db"), [sig(svc("db"))])
    found = await repo.get_many([web.id, "not-an-incident", db.id])
    assert {key: incident.title for key, incident in found.items()} == {
        web.id: web.title,
        db.id: db.title,
    }
    assert await repo.get_many([]) == {}


async def test_a_finished_incident_keeps_its_status_whatever_a_late_run_writes(repo):
    incident, _ = await repo.open_or_attach(svc(), [sig(svc())])
    assert await repo.close_unfinished(incident.id) == "open"
    for late in ("executing", "investigating", "resolved", "failed"):
        await repo.set_status(incident.id, late)
    assert (await repo.get(incident.id)).status == "closed"
    assert await repo.close_unfinished(incident.id) is None


async def test_open_on_host_lists_the_hosts_unfinished_incidents_its_services_included(repo):
    on_host, _ = await repo.open_or_attach(host("c"), [sig(host("c"), "disk")])
    on_service, _ = await repo.open_or_attach(svc("c/blog", "c"), [sig(svc("c/blog", "c"))])
    elsewhere, _ = await repo.open_or_attach(svc(), [sig(svc())])
    done, _ = await repo.open_or_attach(svc("c/wiki", "c"), [sig(svc("c/wiki", "c"))])
    await repo.set_status(done.id, "resolved")
    assert {i.id for i in await repo.open_on_host("c")} == {on_host.id, on_service.id}
    assert elsewhere.id not in {i.id for i in await repo.open_on_host("c")}


async def test_close_unfinished_leaves_a_spared_status_open(repo):
    incident, _ = await repo.open_or_attach(svc(), [sig(svc())])
    await repo.set_status(incident.id, "executing")
    assert await repo.close_unfinished(incident.id, spare=("executing", "verifying")) is None
    assert (await repo.get(incident.id)).status == "executing"
    assert await repo.close_unfinished(incident.id) == "executing"


async def test_incident_tokens_and_cost_are_the_usage_ledger_sums(repo):
    incident, _ = await repo.open_or_attach(svc(), [sig(svc())])
    other, _ = await repo.open_or_attach(svc("db"), [sig(svc("db"))])
    assert (incident.tokens, incident.cost_usd) == (0, None)

    ledger = CostLedger(repo.db)
    await ledger.add(incident.id, "m", 100, 20, 0.5)
    await ledger.add(incident.id, "m", 50, 5, 0.25)
    await ledger.add(other.id, "m", 7, 3, None)

    expected = (175, 0.75)
    for read in (
        await repo.get(incident.id),
        (await repo.get_many([incident.id]))[incident.id],
        (await repo.list(service_id="web"))[0],
        next(i for i in await repo.unfinished() if i.id == incident.id),
    ):
        assert (read.tokens, read.cost_usd) == expected
    assert (await repo.get(other.id)).tokens == 10
    assert (await repo.get(other.id)).cost_usd is None


# --- folded repeats ---


class Hours:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def later(self, hours: float) -> None:
        self.now += timedelta(hours=hours)


@pytest.fixture
async def folding(tmp_path):
    db = Database(tmp_path / "fold.sqlite")
    await db.migrate()
    clock = Hours()
    window = {"h": 24}
    return IncidentRepo(db, clock=clock, fold_window_h=lambda: window["h"]), clock, window


def died(container="web-1", kind="container_died") -> Signal:
    return Signal(subject=svc(), kind=kind, at=T0, container=container, detail={"exit_code": 1})


async def again(repo, clock, signal, hours=2):
    """Close the subject's open incident, wait, and open the next one."""
    open_id = await repo.open_incident_id(signal.subject)
    if open_id is not None:
        await repo.close_unfinished(open_id)
    clock.later(hours)
    incident, created = await repo.open_or_attach(signal.subject, [signal])
    assert created
    return incident


async def test_a_repeat_within_the_window_joins_the_group_of_the_first(folding):
    repo, clock, _ = folding
    first = await again(repo, clock, died(), 0)
    second = await again(repo, clock, died(kind="down_past_grace"))  # the same death, told late
    third = await again(repo, clock, died(kind="oom"))
    assert first.fold_of is None
    assert second.fold_of == first.id and third.fold_of == first.id  # the root, not the last
    assert second.title.endswith("(seen 2 times since 02 Nov 12:00 UTC)")
    assert third.title.endswith("(seen 3 times since 02 Nov 12:00 UTC)")
    groups = await repo.groups([first, third])
    assert groups[first.id].count == groups[third.id].count == 3
    assert [i.id for i in await repo.list(fold_of=first.id)] == [third.id, second.id]


def test_the_fold_key_is_the_incidents_most_telling_kind_in_the_detectors_order():
    deaths = [died() for _ in range(3)]
    loop = Signal(subject=svc(), kind="restart_loop", at=T0, container="web-1")
    unhealthy = Signal(subject=svc(), kind="unhealthy", at=T0, container="web-1")
    url = Signal(subject=svc(), kind="url_failed", at=T0, detail={"url": "https://x/"})
    assert fold_key(svc(), [*deaths, loop]).endswith("|restart_loop|web-1")
    assert fold_key(svc(), [deaths[0], unhealthy]).endswith("|unhealthy|web-1")
    assert fold_key(svc(), [deaths[0], url]).endswith("|url_failed|https://x/")
    assert fold_key(svc(), deaths).endswith("|death|web-1")


async def test_a_loop_after_a_single_death_is_no_repeat_of_it(folding):
    repo, clock, _ = folding
    loop = Signal(subject=svc(), kind="restart_loop", at=T0, container="web-1")
    await again(repo, clock, died(), 0)
    clock.later(2)
    await repo.close_unfinished(await repo.open_incident_id(svc()))
    escalated, _ = await repo.open_or_attach(svc(), [died(), died(), died(), loop])
    assert escalated.fold_of is None


async def test_another_target_or_kind_is_not_a_repeat_and_a_loop_has_its_own_group(folding):
    repo, clock, _ = folding
    await again(repo, clock, died(), 0)
    assert (await again(repo, clock, died("worker-1"))).fold_of is None
    loop = await again(repo, clock, died(kind="restart_loop"))
    assert loop.fold_of is None
    assert await repo.groups([loop]) == {}  # alone: no group


async def test_the_window_slides_from_the_latest_repeat_and_0_turns_folding_off(folding):
    repo, clock, window = folding
    first = await again(repo, clock, died(), 0)
    for _ in range(3):  # every 20 hours: each is within a day of the one before
        assert (await again(repo, clock, died(), 20)).fold_of == first.id
    assert (await again(repo, clock, died(), 25)).fold_of is None  # more than a day later
    window["h"] = 0
    assert (await again(repo, clock, died(), 1)).fold_of is None
