import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bleep.incidents.repository import IncidentRepo
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
