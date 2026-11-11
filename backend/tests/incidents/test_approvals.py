import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from bleep.graph.deps import StaticSettings
from bleep.incidents.approvals import ApprovalStore, grant_token, plan_hash
from bleep.incidents.reminders import ReminderLoop
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import Decision, PlanStep, Proposal, TargetSnapshot
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SECRET = "test-secret"


def restart(target: str = "shop-web-1") -> Proposal:
    return Proposal(
        kind="act",
        steps=[PlanStep(action="docker.restart", host_id="a", target=target)],
        reason="it exited",
    )


def snap(target: str = "shop-web-1", container_id: str = "c1") -> TargetSnapshot:
    return TargetSnapshot(host_id="a", target=target, container_id=container_id, started_at=T0)


def decide(kind: str = "approve", via: str = "dashboard", text: str | None = None) -> Decision:
    return Decision(kind=kind, text=text, via=via, at=T0)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(tmp_path / "bleep.sqlite")
    await database.migrate()
    return database


async def open_incident(db: Database, service: str = "a/shop") -> str:
    subject = Subject(kind="service", host_id="a", service_id=service)
    incident, _ = await IncidentRepo(db).open_or_attach(
        subject, [Signal(subject=subject, kind="container_died", at=T0)]
    )
    return incident.id


# --- plan hash and grant ---


def test_plan_hash_covers_the_steps_and_the_resolved_container_ids():
    same = plan_hash(restart(), [snap()])
    assert plan_hash(restart(), [snap()]) == same
    assert plan_hash(restart(), [snap(container_id="c2")]) != same  # recreated container
    assert plan_hash(restart("shop-db-1"), [snap("shop-db-1")]) != same  # another target
    stop = restart().model_copy(
        update={"steps": [PlanStep(action="docker.stop", host_id="a", target="shop-web-1")]}
    )
    assert plan_hash(stop, [snap()]) != same


def test_plan_hash_ignores_the_reason_and_the_snapshot_times():
    reworded = restart().model_copy(update={"reason": "said differently"})
    later = snap().model_copy(update={"started_at": T0 + timedelta(hours=1)})
    assert plan_hash(reworded, [later]) == plan_hash(restart(), [snap()])


def test_the_grant_token_is_an_hmac_over_the_incident_id_and_the_plan_hash():
    digest = hmac.new(SECRET.encode(), b"inc_1" + b"h" * 64, hashlib.sha256).hexdigest()
    assert grant_token(SECRET, "inc_1", "h" * 64) == digest
    assert grant_token("other-secret", "inc_1", "h" * 64) != digest
    assert grant_token(SECRET, "inc_2", "h" * 64) != digest


async def test_grant_tokens_differ_when_a_steps_target_changes(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    tokens = []
    for proposal, snapshots in [
        (restart(), [snap()]),
        (restart(), [snap(container_id="c2")]),  # same name, recreated container
        (restart("shop-db-1"), [snap("shop-db-1", "c3")]),  # another container
    ]:
        await store.request(incident_id, proposal, snapshots)
        assert await store.resolve(incident_id, decide("approve")) is not None
        grant = await store.grant(incident_id)
        assert grant is not None and grant.plan_hash == plan_hash(proposal, snapshots)
        tokens.append(grant.token)
    assert len(set(tokens)) == 3


async def test_a_grant_exists_only_for_an_approved_plan(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    assert await store.grant(incident_id) is None  # never asked

    request = await store.request(incident_id, restart(), [snap()])
    assert await store.grant(incident_id) is None  # pending
    await store.resolve(incident_id, decide("reject"))
    assert await store.grant(incident_id) is None  # rejected

    request = await store.request(incident_id, restart(), [snap()])
    await store.resolve(incident_id, decide("approve"))
    grant = await store.grant(incident_id)
    assert grant is not None
    assert grant.incident_id == incident_id
    assert grant.token == grant_token(SECRET, incident_id, request.plan_hash)


# --- request and resolve ---


async def test_request_stores_the_plan_and_its_targets(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    request = await store.request(incident_id, restart(), [snap()])

    record = await store.current(incident_id)
    assert record is not None
    assert record.id == request.id and record.plan_hash == request.plan_hash
    assert record.proposal == restart()
    assert record.snapshots == [snap()]
    assert record.requested_at == T0
    assert record.decision is None


async def test_two_concurrent_decisions_exactly_one_wins(db):
    incident_id = await open_incident(db)
    await ApprovalStore(db, SECRET, Clock()).request(incident_id, restart(), [snap()])
    # Two channels, each with its own store and connection, answer at the same moment.
    telegram, dashboard = ApprovalStore(db, SECRET), ApprovalStore(db, SECRET)
    attempts = [
        (telegram if i % 2 else dashboard).resolve(
            incident_id,
            decide("approve" if i % 2 else "reject", "telegram" if i % 2 else "dashboard"),
        )
        for i in range(10)
    ]
    results = await asyncio.gather(*attempts)

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    record = await telegram.current(incident_id)
    assert record is not None and record.decision == winners[0]


async def test_a_second_decision_loses_and_the_first_stands(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    await store.request(incident_id, restart(), [snap()])

    first = decide("reject", "telegram")
    assert await store.resolve(incident_id, first) == first
    assert await store.resolve(incident_id, decide("approve", "dashboard")) is None
    record = await store.current(incident_id)
    assert record is not None and record.decision == first
    assert await store.grant(incident_id) is None


async def test_a_decision_without_a_request_wins_nothing(db):
    incident_id = await open_incident(db)
    assert await ApprovalStore(db, SECRET).resolve(incident_id, decide()) is None


async def test_a_decision_for_a_superseded_approval_wins_nothing(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    first = await store.request(incident_id, restart(), [snap()])
    await store.resolve(incident_id, decide("instruct", text="restart the db too"), first.id)
    second = await store.request(incident_id, restart("shop-db-1"), [snap("shop-db-1")])

    assert await store.resolve(incident_id, decide("approve", "telegram"), first.id) is None
    record = await store.current(incident_id)
    assert record is not None and record.id == second.id and record.decision is None
    assert await store.grant(incident_id) is None
    assert await store.resolve(incident_id, decide("approve"), second.id) is not None


async def test_current_is_the_latest_request(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    await store.request(incident_id, restart(), [snap()])
    await store.resolve(incident_id, decide("instruct", text="restart the db too"))
    second = await store.request(incident_id, restart("shop-db-1"), [snap("shop-db-1")])

    record = await store.current(incident_id)
    assert record is not None and record.id == second.id and record.decision is None


# --- reminders ---


class RecordingNotifier:
    def __init__(self) -> None:
        self.reminders: list[tuple[str, str]] = []

    async def reminder(self, incident, approval) -> None:
        self.reminders.append((incident.id, approval.id))


async def reminder_env(db: Database):
    clock = Clock()
    store = ApprovalStore(db, SECRET, clock)
    notifier = RecordingNotifier()
    loop = ReminderLoop(store, IncidentRepo(db), notifier, StaticSettings())  # type: ignore[arg-type]
    return store, notifier, loop


async def test_reminders_fire_every_60_minutes_and_never_decide(db):
    incident_id = await open_incident(db)
    store, notifier, loop = await reminder_env(db)
    request = await store.request(incident_id, restart(), [snap()])

    sent = []
    for minutes in (0, 30, 59, 60, 61, 90, 119, 120, 121):
        if await loop.tick(T0 + timedelta(minutes=minutes)):
            sent.append(minutes)
    assert sent == [60, 120]
    assert notifier.reminders == [(incident_id, request.id)] * 2

    await loop.tick(T0 + timedelta(hours=24))
    record = await store.current(incident_id)
    assert record is not None and record.decision is None  # still pending after a day
    assert await store.grant(incident_id) is None


async def test_a_reminder_after_downtime_is_sent_once(db):
    incident_id = await open_incident(db)
    store, notifier, loop = await reminder_env(db)
    await store.request(incident_id, restart(), [snap()])

    await loop.tick(T0 + timedelta(hours=5, minutes=10))  # Bleep was down for five hours
    await loop.tick(T0 + timedelta(hours=5, minutes=20))
    assert len(notifier.reminders) == 1
    await loop.tick(T0 + timedelta(hours=6))
    assert len(notifier.reminders) == 2


async def test_no_reminder_once_decided_or_for_a_closed_incident(db):
    decided = await open_incident(db, "a/shop")
    closed = await open_incident(db, "a/blog")
    store, notifier, loop = await reminder_env(db)
    await store.request(decided, restart(), [snap()])
    await store.request(closed, restart(), [snap()])
    await store.resolve(decided, decide("reject"))
    await IncidentRepo(db).set_status(closed, "closed")

    assert await loop.tick(T0 + timedelta(hours=2)) == []
    assert notifier.reminders == []


async def test_the_reminder_period_comes_from_the_settings(db):
    incident_id = await open_incident(db)
    store, notifier, _ = await reminder_env(db)
    settings = StaticSettings()
    settings.budget_limits = settings.budget_limits.model_copy(update={"approval_reminder_min": 15})
    loop = ReminderLoop(store, IncidentRepo(db), notifier, settings)  # type: ignore[arg-type]
    await store.request(incident_id, restart(), [snap()])

    assert await loop.tick(T0 + timedelta(minutes=15)) != []
    assert await loop.tick(T0 + timedelta(minutes=29)) == []
    assert await loop.tick(T0 + timedelta(minutes=30)) != []


# --- refused plans (#113) ---


def refused() -> Proposal:
    return Proposal(kind="no_action", reason="refused", refused=True)


async def test_a_refused_plan_cannot_be_approved_but_can_be_instructed_or_let_go(db):
    store = ApprovalStore(db, SECRET, Clock())
    first = await open_incident(db, "a/one")
    await store.request(first, refused(), [])
    assert await store.resolve(first, decide("approve")) is None
    assert (await store.current(first)).decision is None
    assert await store.grant(first) is None
    assert await store.resolve(first, decide("instruct", text="x")) is not None

    second = await open_incident(db, "a/two")
    await store.request(second, refused(), [])
    assert await store.resolve(second, decide("reject")) is not None


async def test_a_plan_stored_before_the_refused_flag_can_still_be_approved(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    request = await store.request(incident_id, restart(), [snap()])
    old = restart().model_dump(mode="json")
    for key in ("refused", "refused_steps", "refusal_reasons"):
        old.pop(key)
    conn = await db.connect()
    try:
        await conn.execute("UPDATE approvals SET proposal = ?", (json.dumps(old),))
        await conn.commit()
    finally:
        await conn.close()

    assert await store.resolve(incident_id, decide("approve"), request.id) is not None


async def test_the_in_memory_approvals_refuse_an_approve_on_a_refused_plan_too():
    from bleep.graph.deps import InMemoryApprovals

    store = InMemoryApprovals(SECRET)
    await store.request("inc", refused(), [])

    assert await store.resolve("inc", decide("approve")) is None
    assert await store.resolve("inc", decide("reject")) is not None


@pytest.mark.parametrize("fix_runnable", [True, False])
async def test_no_plan_of_no_action_can_be_approved_in_either_store(db, fix_runnable):
    """#365: a no-action plan whose fix Bleep could run is put to the person too; whatever marks
    it, nothing that runs nothing is approvable."""
    from bleep.graph.deps import InMemoryApprovals

    plan = Proposal(kind="no_action", reason="x", fix_runnable=fix_runnable)
    incident_id = await open_incident(db)
    for store in (ApprovalStore(db, SECRET, Clock()), InMemoryApprovals(SECRET)):
        await store.request(incident_id, plan, [])
        assert await store.resolve(incident_id, decide("approve")) is None
        assert await store.grant(incident_id) is None
        assert await store.resolve(incident_id, decide("instruct", text="x")) is not None


async def test_a_closed_incidents_pending_approval_cannot_be_decided_from_any_channel(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    request = await store.request(incident_id, restart(), [snap()])
    await IncidentRepo(db).set_status(incident_id, "closed")  # its host was removed

    for via in ("dashboard", "telegram"):
        assert await store.resolve(incident_id, decide(via=via), request.id) is None
    assert await store.grant(incident_id) is None
    assert await store.pending() == []


# --- an incident that ends before anyone answered (#277) ---


@pytest.mark.parametrize("status", ["closed", "failed", "resolved"])
async def test_an_incident_that_finishes_closes_its_unanswered_approval(db, status):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    request = await store.request(incident_id, restart(), [snap()])
    await IncidentRepo(db).set_status(incident_id, status)

    record = await store.current(incident_id)
    assert record is not None and record.id == request.id
    assert record.closed and record.decision is None
    assert await store.resolve(incident_id, decide(), request.id) is None
    assert await store.grant(incident_id) is None


async def test_a_close_from_outside_the_run_closes_the_unanswered_approval(db):
    """Quiet and a removed host both end the incident through `close_unfinished`."""
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    await store.request(incident_id, restart(), [snap()])
    await IncidentRepo(db).set_status(incident_id, "awaiting_approval")
    await IncidentRepo(db).close_unfinished(incident_id)

    record = await store.current(incident_id)
    assert record is not None and record.closed and record.decision is None


async def test_a_decided_approval_stays_as_decided_when_its_incident_finishes(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    await store.request(incident_id, restart(), [snap()])
    await store.resolve(incident_id, decide("reject", "telegram"))
    await IncidentRepo(db).close_unfinished(incident_id)

    record = await store.current(incident_id)
    assert record is not None and not record.closed
    assert record.decision is not None and record.decision.kind == "reject"


async def test_a_still_open_incidents_approval_is_not_closed(db):
    incident_id = await open_incident(db)
    store = ApprovalStore(db, SECRET, Clock())
    await store.request(incident_id, restart(), [snap()])
    await IncidentRepo(db).set_status(incident_id, "awaiting_approval")

    record = await store.current(incident_id)
    assert record is not None and not record.closed and record.decision is None


async def test_a_yes_racing_the_close_either_wins_first_or_finds_the_approval_closed(db):
    """Each round races a yes on its own connection against the close on another. The yes wins
    only before the close (then the approval holds it and the grant exists, and the runner and
    Execute refuse the closed incident); after it, the approval is closed and nothing is won."""
    store = ApprovalStore(db, SECRET, Clock())
    for n in range(20):
        incident_id = await open_incident(db, f"a/shop{n}")
        request = await store.request(incident_id, restart(), [snap()])
        yes = ApprovalStore(db, SECRET).resolve(incident_id, decide(), request.id)
        close = IncidentRepo(db).close_unfinished(incident_id)
        if n % 2:
            won, before = await asyncio.gather(yes, close)
        else:
            before, won = await asyncio.gather(close, yes)

        record = await store.current(incident_id)
        assert before is not None  # the close always went through
        assert (await IncidentRepo(db).get(incident_id)).status == "closed"
        if won is not None:
            assert record.decision == won and not record.closed
            assert await store.grant(incident_id) is not None
        else:
            assert record.closed and record.decision is None
            assert await store.grant(incident_id) is None
    assert await store.pending() == []


async def test_a_question_stored_after_its_incident_closed_is_closed_at_once(db):
    """The run's Approval node can commit its question just after an outside close: `end`
    closes the incident before the cancel of the run reaches it (#277)."""
    incident_id = await open_incident(db)
    await IncidentRepo(db).close_unfinished(incident_id)
    store = ApprovalStore(db, SECRET, Clock())
    request = await store.request(incident_id, restart(), [snap()])

    record = await store.current(incident_id)
    assert record is not None and record.id == request.id
    assert record.closed and record.decision is None
    assert await store.resolve(incident_id, decide(), request.id) is None


async def test_the_in_memory_approvals_close_an_unanswered_approval_like_the_store(db):
    from bleep.graph.deps import InMemoryApprovals

    repo = IncidentRepo(db)
    answered, unanswered, late = [await open_incident(db, f"a/s{n}") for n in range(3)]
    store = InMemoryApprovals(SECRET, repo)
    for incident_id in (answered, unanswered):
        await store.request(incident_id, restart(), [snap()])
    await store.resolve(answered, decide("reject"))
    for incident_id in (answered, unanswered, late):
        await repo.close_unfinished(incident_id)
    await store.request(late, restart(), [snap()])  # stored after the close

    assert not (await store.current(answered)).closed
    for incident_id in (unanswered, late):
        record = await store.current(incident_id)
        assert record.closed and record.decision is None
        assert await store.resolve(incident_id, decide()) is None
        assert await store.grant(incident_id) is None
