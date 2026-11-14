import asyncio
from datetime import UTC, datetime, timedelta

from bleep.graph.shown import english
from bleep.incidents.approvals import ApprovalStore
from bleep.incidents.repository import IncidentRepo
from bleep.schemas.graph import Decision, PlanStep, Proposal, VerificationResult
from bleep.schemas.incidents import LOOK_REQUESTED, Incident, Signal, Subject
from bleep.schemas.shown import Shown
from bleep.store.db import Database
from bleep.watcher.recovery import HOLD, RecoveryWatch

T0 = datetime(2026, 11, 13, 17, 51, tzinfo=UTC)
SHOP = Subject(kind="service", host_id="h", service_id="h/compose:shop")
PLAN = Proposal(
    kind="act",
    steps=[PlanStep(action="docker.restart", host_id="h", target="shop-web-1")],
    reason="it exited",
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Check:
    """Verify's checks as the watch sees them: the first call of a hold takes the first reads
    and proves nothing; a later one passes unless `ok` says otherwise."""

    def __init__(self) -> None:
        self.calls = 0
        self.ok = True
        self.raises = False

    async def __call__(self, subject, signals, first_running) -> VerificationResult:
        self.calls += 1
        if self.raises:
            raise RuntimeError("the host's engine broke mid-read")
        if not first_running:
            first_running["shop-web-1"] = object()
            return VerificationResult(ok=False)
        return VerificationResult(ok=self.ok)


class Runner:
    def __init__(self, repo: IncidentRepo) -> None:
        self.repo = repo
        self.told: list[tuple[str, str]] = []

    async def healthy_again(self, incident_id: str, text: Shown) -> bool:
        if not await self.repo.close_unanswered(incident_id):
            return False
        self.told.append((incident_id, english(text)))
        return True


class Rig:
    def __init__(self, tmp_path) -> None:
        self.clock = Clock()
        self.db = Database(tmp_path / "bleep.sqlite")
        self.repo = IncidentRepo(self.db, self.clock)
        self.approvals = ApprovalStore(self.db, "test-secret", self.clock)
        self.runner = Runner(self.repo)
        self.check = Check()
        self.closed: list[str] = []

        async def closed(incident: Incident) -> None:
            self.closed.append(incident.id)

        self.watch = RecoveryWatch(
            self.repo,
            self.runner,  # type: ignore[arg-type]
            self.check,
            self.clock,
            lambda subject: "shop",
            closed=closed,
        )

    async def boot(self) -> Rig:
        await self.db.migrate()
        return self

    async def incident(
        self, subject: Subject = SHOP, status: str = "awaiting_approval", kind="container_died"
    ):
        signal = self.signal(subject).model_copy(update={"kind": kind})
        incident, _ = await self.repo.open_or_attach(subject, [signal])
        await self.approvals.request(incident.id, PLAN, [])
        await self.repo.set_status(incident.id, status)
        return incident

    def signal(self, subject: Subject = SHOP) -> Signal:
        at = self.clock()
        return Signal(subject=subject, kind="container_died", at=at, container="shop-web-1")

    async def healthy_for(self, seconds: float, step: float = 15) -> None:
        """One judgement now, then one every `step` seconds for `seconds`, all healthy."""
        await self.watch.judge("h", {SHOP.service_id})
        for _ in range(int(seconds // step)):
            self.clock.advance(step)
            await self.watch.judge("h", {SHOP.service_id})

    async def status(self, incident_id: str) -> str:
        return (await self.repo.get(incident_id)).status


async def test_a_service_held_healthy_for_five_minutes_closes_its_waiting_incident_once(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()

    await r.healthy_for(HOLD.total_seconds())
    await r.healthy_for(60)  # later ticks find nothing left to close

    assert await r.status(incident.id) == "closed"
    assert r.runner.told == [(incident.id, english(Shown.of("notice/recovered", name="shop")))]
    assert r.closed == [incident.id]
    assert r.check.calls == 2  # the first reads, and the check against them


async def test_healthy_for_less_than_the_hold_leaves_it_waiting(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()

    await r.healthy_for(HOLD.total_seconds() - 15)

    assert await r.status(incident.id) == "awaiting_approval"
    assert r.runner.told == [] and r.check.calls == 1


async def test_a_flap_during_the_hold_starts_it_again(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    await r.healthy_for(240)
    r.clock.advance(15)
    await r.watch.judge("h", set())  # one tick down: the fault is back for a moment
    r.clock.advance(15)

    await r.healthy_for(HOLD.total_seconds() - 15)
    assert await r.status(incident.id) == "awaiting_approval"
    await r.healthy_for(15)

    assert await r.status(incident.id) == "closed"


async def test_a_new_signal_on_the_incident_starts_the_hold_again(tmp_path):
    """A death between two ticks that left the service healthy at both: the watcher attached
    it to the incident."""
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    await r.healthy_for(240)
    await r.repo.open_or_attach(SHOP, [r.signal()])

    await r.healthy_for(HOLD.total_seconds() - 15)
    assert await r.status(incident.id) == "awaiting_approval"
    await r.healthy_for(15)

    assert await r.status(incident.id) == "closed"


async def test_verifys_checks_failing_at_the_end_of_the_hold_keep_it_open_and_start_again(
    tmp_path,
):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    r.check.ok = False  # a container was started again since the first reads, say

    await r.healthy_for(HOLD.total_seconds())
    assert await r.status(incident.id) == "awaiting_approval"
    assert r.check.calls == 2
    r.check.ok = True
    await r.healthy_for(HOLD.total_seconds())

    assert await r.status(incident.id) == "closed"
    assert r.check.calls == 4  # a new hold took its own first reads


async def test_an_incident_still_investigating_is_not_held(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident(status="investigating")

    await r.healthy_for(HOLD.total_seconds())

    assert await r.status(incident.id) == "investigating"
    assert r.check.calls == 0
    await r.repo.set_status(incident.id, "awaiting_approval")  # the plan is asked for now
    await r.healthy_for(HOLD.total_seconds() - 15)
    assert await r.status(incident.id) == "awaiting_approval"  # its hold starts only now


async def test_a_host_incident_waiting_for_a_yes_is_left_alone(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident(subject=Subject(kind="host", host_id="h"))

    await r.healthy_for(HOLD.total_seconds())

    assert await r.status(incident.id) == "awaiting_approval"
    assert r.check.calls == 0


async def test_a_host_that_cannot_be_judged_ends_the_hold(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    await r.healthy_for(240)
    r.watch.forget_host("h")  # Bleep woke from its own sleep, say

    await r.healthy_for(HOLD.total_seconds() - 15)

    assert await r.status(incident.id) == "awaiting_approval"


async def test_a_yes_that_won_first_keeps_the_incident_open_and_hears_nothing(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    await r.healthy_for(HOLD.total_seconds() - 15)
    yes = Decision(kind="approve", via="telegram", at=r.clock())
    assert await r.approvals.resolve(incident.id, yes) is not None

    await r.healthy_for(15)

    assert await r.status(incident.id) == "awaiting_approval"  # its resume drives it on
    assert r.runner.told == [] and r.closed == []


async def test_the_poll_tick_and_the_url_round_at_once_close_it_once(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    await r.healthy_for(HOLD.total_seconds() - 15)
    r.clock.advance(15)

    await asyncio.gather(*(r.watch.judge("h", {SHOP.service_id}) for _ in range(2)))

    assert await r.status(incident.id) == "closed"
    assert len(r.runner.told) == 1 and r.closed == [incident.id]


async def test_a_check_that_raises_costs_nothing_but_its_hold(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()
    r.check.raises = True

    await r.healthy_for(60)  # never raises out of the tick
    r.check.raises = False
    await r.healthy_for(HOLD.total_seconds())

    assert await r.status(incident.id) == "closed"


async def test_another_service_on_the_host_being_healthy_does_not_hold_this_one(tmp_path):
    r = await Rig(tmp_path).boot()
    incident = await r.incident()

    for _ in range(int(HOLD.total_seconds() // 15) + 1):
        await r.watch.judge("h", {"h/compose:blog"})
        r.clock.advance(15)

    assert await r.status(incident.id) == "awaiting_approval"
    assert r.check.calls == 0


async def test_a_look_a_person_asked_for_is_never_closed_as_healthy_again(tmp_path):
    """A look's service may have been healthy all along: its plan answers the person (#403)."""
    r = await Rig(tmp_path).boot()
    look = await r.incident(kind=LOOK_REQUESTED)

    await r.healthy_for(2 * HOLD.total_seconds())

    assert await r.status(look.id) == "awaiting_approval"
    assert r.check.calls == 0 and r.runner.told == []
