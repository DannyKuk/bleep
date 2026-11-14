"""`GET /api/usage`: every number of the Usage page, summed from a seeded database (#547)."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.schemas.graph import HEALTHY_ANYWAY, OUTCOME_TEXT, PlanStep, Proposal
from bleep.schemas.incidents import Signal, Subject

NOW = datetime(2026, 11, 13, 12, 0)  # the machine's local time the ledger sees
OPENED = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
OUTCOME_KEYS = set(OUTCOME_TEXT)


class World:
    """A logged-in client over an app whose ledger and incident store run on `now`."""

    def __init__(self, client: TestClient, parts) -> None:
        self.client, self.parts = client, parts
        self.now = NOW
        parts.graph_deps.ledger.clock = lambda: self.now
        self.opened = OPENED
        parts.incidents = IncidentRepo(parts.db, lambda: self.opened)

    def call(self, fn, *args, **kwargs):
        return self.client.portal.call(lambda: fn(*args, **kwargs))

    def open(self, service: str, days_ago: int = 0, at: datetime | None = None) -> str:
        self.opened = at or OPENED - timedelta(days=days_ago)
        subject = Subject(kind="service", host_id="a", service_id=service)
        signal = Signal(subject=subject, kind="container_died", at=self.opened, container="web-1")
        incident, _ = self.call(self.parts.incidents.open_or_attach, subject, [signal])
        return incident.id

    def report(self, incident_id: str, outcome: str, status: str = "closed") -> None:
        self.call(self.parts.bus.publish, incident_id, "report", {"outcome": outcome})
        self.call(self.parts.incidents.set_status, incident_id, status)

    def spend(
        self,
        incident_id: str,
        model: str,
        usd: float | None,
        provider: str | None = "openrouter",
        days_ago: int = 0,
        tokens: tuple[int, int] = (1000, 100),
        cache: tuple[int, int] = (0, 0),
    ) -> None:
        self.now = NOW - timedelta(days=days_ago)
        self.call(
            self.parts.graph_deps.ledger.add,
            incident_id,
            model,
            *tokens,
            usd,
            *cache,
            provider,
        )
        self.now = NOW

    def usage(self, days: int | None = None) -> dict:
        response = self.client.get("/api/usage", params={} if days is None else {"days": days})
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def world(settings, parts):
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield World(client, parts)


def test_an_empty_range_is_all_zeros_and_the_caps_are_still_there(world):
    body = world.usage()

    assert body["days"] == 30
    assert body["totals"] == {
        "cost_usd": 0,
        "calls": 0,
        "incident_calls": 0,
        "chat_calls": 0,
        "unpriced_calls": 0,
        "local_calls": 0,
        "unpriced_local_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert (body["models"], body["per_day"], body["actions"], body["guard"]) == ([], [], [], [])
    assert body["incidents"] == {"count": 0, "average_cost_usd": None, "most_expensive": None}
    assert body["endings"] == {
        "outcomes": dict.fromkeys(OUTCOME_KEYS, 0),
        "failed": 0,
        "open": 0,
        "other": 0,
    }
    assert body["today"] == {
        "spent_usd": 0,
        "daily_cap_usd": 1.0,
        "chat_spent_usd": 0,
        "chat_daily_cap_usd": 0.2,
    }


def test_local_calls_are_free_and_a_local_model_without_a_price_is_counted_unpriced(world):
    inc = world.open("a/shop")
    world.spend(inc, "openai/gpt-oss-20b", 0.0, "local")  # in the price table: priced at 0
    world.spend(inc, "my-own-model", None, "local")
    world.spend(inc, "my-own-model", None, "local")
    world.spend(inc, "claude-sonnet-5-5", 0.25, "anthropic")
    world.spend(inc, "mystery/m", None, "openrouter")  # unpriced, but not a local call
    world.spend(inc, "old-model", None, None)  # from before the provider column

    body = world.usage()

    totals = body["totals"]
    assert (totals["calls"], totals["local_calls"], totals["unpriced_calls"]) == (6, 3, 4)
    assert totals["unpriced_local_calls"] == 2
    assert totals["cost_usd"] == pytest.approx(0.25)
    by = {(m["model"], m["provider"]): m for m in body["models"]}
    assert (
        by["my-own-model", "local"]["calls"],
        by["my-own-model", "local"]["unpriced_calls"],
    ) == (
        2,
        2,
    )
    assert by["my-own-model", "local"]["cost_usd"] == 0
    assert by["openai/gpt-oss-20b", "local"]["unpriced_calls"] == 0
    assert body["models"][0]["model"] == "my-own-model"  # the model with the most calls first
    assert body["incidents"]["average_cost_usd"] == pytest.approx(0.25)  # only priced incidents


def test_one_model_through_two_providers_is_two_rows_and_an_older_row_is_unknown(world):
    inc = world.open("a/shop")
    world.spend(inc, "claude-sonnet-5-5", 0.3, "anthropic")
    world.spend(inc, "claude-sonnet-5-5", 0.4, "openrouter")
    world.spend(inc, "claude-sonnet-5-5", 0.1, None)

    models = world.usage()["models"]

    assert sorted((m["provider"], m["calls"], m["cost_usd"]) for m in models) == [
        ("anthropic", 1, 0.3),
        ("openrouter", 1, 0.4),
        ("unknown", 1, 0.1),
    ]


def test_chat_and_incident_calls_on_the_same_day_are_split_everywhere(world):
    inc = world.open("a/shop")
    world.spend(inc, "m", 0.5)
    world.spend(inc, "m", 0.25)
    world.spend("talk", "m", 0.125)
    world.spend("recovery", "m", 0.0625)  # a recovery line belongs to no incident, not to chat
    world.spend("talk", "other", 0.03125)

    body = world.usage()

    totals = body["totals"]
    assert (totals["calls"], totals["incident_calls"], totals["chat_calls"]) == (5, 3, 2)
    assert totals["cost_usd"] == pytest.approx(0.96875)
    day = {row["model"]: row for row in body["per_day"]}
    assert set(day) == {"m", "other"}
    assert {row["day"] for row in body["per_day"]} == {"2026-11-13"}
    assert (day["m"]["incident_calls"], day["m"]["chat_calls"]) == (3, 1)
    assert day["m"]["cost_usd"] == pytest.approx(0.9375)
    assert (day["other"]["incident_calls"], day["other"]["chat_calls"]) == (0, 1)
    today = body["today"]
    assert today["spent_usd"] == pytest.approx(0.8125)  # without chat, as the daily cap counts
    assert today["chat_spent_usd"] == pytest.approx(0.15625)


def test_days_are_local_days_and_the_range_cuts_what_is_older(world):
    inc = world.open("a/shop")
    world.spend(inc, "m", 1.0, days_ago=0)
    world.spend(inc, "m", 2.0, days_ago=6)
    world.spend(inc, "m", 4.0, days_ago=7)
    world.spend(inc, "m", 8.0, days_ago=29)
    world.spend(inc, "m", 16.0, days_ago=30)
    world.spend(inc, "m", 32.0, days_ago=89)
    world.spend(inc, "m", 64.0, days_ago=90)

    costs = {days: world.usage(days) for days in (1, 7, 30, 90)}

    assert [costs[d]["totals"]["cost_usd"] for d in (1, 7, 30, 90)] == [1.0, 3.0, 15.0, 63.0]
    assert [costs[d]["totals"]["calls"] for d in (1, 7, 30, 90)] == [1, 2, 4, 6]
    assert [row["day"] for row in costs[7]["per_day"]] == ["2026-11-07", "2026-11-13"]
    assert costs[1]["today"] == costs[90]["today"]  # the caps do not follow the range
    assert costs[1]["today"]["spent_usd"] == pytest.approx(1.0)


def test_cache_tokens_are_summed_in_the_totals_and_per_model(world):
    inc = world.open("a/shop")
    world.spend(inc, "claude-sonnet-5-5", 0.1, "anthropic", tokens=(1000, 50), cache=(600, 100))
    world.spend(inc, "claude-sonnet-5-5", 0.1, "anthropic", tokens=(2000, 50), cache=(1500, 0))
    world.spend(inc, "m", 0.1, "openrouter", tokens=(500, 10))

    body = world.usage()

    assert body["totals"]["cache_read_tokens"] == 2100
    assert body["totals"]["cache_write_tokens"] == 100
    assert (body["totals"]["tokens_in"], body["totals"]["tokens_out"]) == (3500, 110)
    by = {m["model"]: m for m in body["models"]}
    claude = by["claude-sonnet-5-5"]
    assert (claude["tokens_in"], claude["tokens_out"]) == (3000, 100)
    assert (claude["cache_read_tokens"], claude["cache_write_tokens"]) == (2100, 100)
    assert by["m"]["cache_read_tokens"] == 0


def test_an_incident_with_no_report_yet_counts_as_open_and_its_calls_still_cost(world):
    waiting = world.open("a/shop")
    world.spend(waiting, "m", 0.2)
    done = world.open("a/worker")
    world.spend(done, "m", 0.6)
    world.report(done, OUTCOME_TEXT["resolved"], status="resolved")

    body = world.usage()

    assert body["incidents"]["count"] == 2
    assert body["incidents"]["average_cost_usd"] == pytest.approx(0.4)
    assert body["incidents"]["most_expensive"] == {
        "incident_id": done,
        "name": "worker #1",
        "cost_usd": pytest.approx(0.6),
    }
    endings = body["endings"]
    assert (endings["open"], endings["failed"], endings["other"]) == (1, 0, 0)
    assert endings["outcomes"]["resolved"] == 1


def test_a_failed_run_is_counted_apart_from_the_reports_outcomes(world):
    failed = world.open("a/shop")
    world.call(world.parts.incidents.set_status, failed, "failed")
    ok = world.open("a/worker")
    world.report(ok, OUTCOME_TEXT["rejected"])

    endings = world.usage()["endings"]

    assert (endings["failed"], endings["open"], endings["other"]) == (1, 0, 0)
    assert endings["outcomes"]["rejected"] == 1
    assert sum(endings["outcomes"].values()) == 1


def test_every_incident_is_in_one_count_by_the_outcome_its_report_states(world):
    texts = {
        "resolved": OUTCOME_TEXT["resolved"],
        "healthy": OUTCOME_TEXT["healthy"],
        "rejected": OUTCOME_TEXT["rejected"],
        "no_action": OUTCOME_TEXT["no_action"],
        "unverified": OUTCOME_TEXT["unverified"],
        "budget": OUTCOME_TEXT["budget"] + " (the daily cap)",  # an early stop adds to its text
        "replan_limit": OUTCOME_TEXT["replan_limit"],
    }
    for i, text in enumerate(texts.values()):
        world.report(world.open(f"a/svc{i}"), text)
    world.report(world.open("a/healthy-anyway"), HEALTHY_ANYWAY)
    world.report(world.open("a/odd"), "Some text no outcome starts")
    world.call(world.parts.incidents.set_status, world.open("a/closed-quietly"), "closed")
    back = world.open("a/back-on-its-own")  # closed while its plan waited, with no report (#571)
    world.call(world.parts.incidents.set_status, back, "awaiting_approval")
    world.call(world.parts.approvals.request, back, Proposal(kind="no_action", reason="r"), [])
    assert world.call(world.parts.incidents.close_unanswered, back)

    body = world.usage()

    outcomes = body["endings"]["outcomes"]
    assert outcomes == {kind: 2 if kind == "healthy" else 1 for kind in [*texts, "recovered"]}
    assert body["endings"]["other"] == 2  # the odd text, and a close with no report
    assert body["endings"]["open"] == body["endings"]["failed"] == 0
    assert body["incidents"]["count"] == sum(outcomes.values()) + 2


def test_incidents_opened_before_the_range_are_not_counted(world):
    old = world.open("a/old", days_ago=10)
    world.report(old, OUTCOME_TEXT["resolved"])
    world.spend(old, "m", 5.0, days_ago=10)
    new = world.open("a/new", days_ago=2)
    world.spend(new, "m", 1.0, days_ago=2)

    week, month = world.usage(7), world.usage(30)

    assert week["incidents"]["count"] == 1
    assert week["incidents"]["most_expensive"]["incident_id"] == new
    assert week["endings"]["outcomes"]["resolved"] == 0
    assert month["incidents"]["count"] == 2
    assert month["incidents"]["most_expensive"]["incident_id"] == old
    assert month["endings"]["outcomes"]["resolved"] == 1


def test_actions_are_counted_by_action_and_status_and_the_guard_by_kind(world):
    inc = world.open("a/shop")
    log = world.parts.graph_deps.action_log
    restart = PlanStep(action="docker.restart", host_id="a", target="web-1")
    stop = PlanStep(action="docker.stop", host_id="a", target="web-1")
    world.call(log.start, inc, "k1", 0, restart)
    world.call(log.start, inc, "k2", 0, restart)
    world.call(log.skip, inc, "k1", 1, stop, "an earlier step failed")
    world.parts.guard_log.record(inc, "refused_command", "rm -rf /")
    world.parts.guard_log.record(inc, "refused_command", "dd if=/dev/zero")
    world.parts.guard_log.record(inc, "redacted", "a token")

    body = world.usage()

    assert body["actions"] == [
        {"action": "docker.restart", "status": "started", "count": 2},
        {"action": "docker.stop", "status": "skipped", "count": 1},
    ]
    assert body["guard"] == [
        {"kind": "redacted", "count": 1},
        {"kind": "refused_command", "count": 2},
    ]
    assert world.usage(1)["actions"] == body["actions"]


def test_actions_and_guard_entries_from_before_the_range_are_left_out(world):
    old, new = world.open("a/old", days_ago=10), world.open("a/new")
    log, guard = world.parts.graph_deps.action_log, world.parts.guard_log
    restart = PlanStep(action="docker.restart", host_id="a", target="web-1")
    stop = PlanStep(action="docker.stop", host_id="a", target="web-1")
    start = PlanStep(action="docker.start", host_id="a", target="web-1")
    log.clock, guard.clock = (
        lambda: OPENED - timedelta(days=10),
        lambda: OPENED - timedelta(days=10),
    )
    world.call(log.start, old, "k1", 0, restart)
    guard.record(old, "refused_command", "rm -rf /")
    world.call(log.skip, old, "k1", 1, stop, "an earlier step failed")  # no time: its incident's
    log.clock, guard.clock = lambda: OPENED, lambda: OPENED
    world.call(log.start, new, "k2", 0, start)
    world.call(log.skip, new, "k2", 1, stop, "an earlier step failed")
    guard.record(new, "redacted", "a token")

    week, month = world.usage(7), world.usage(30)

    assert week["actions"] == [
        {"action": "docker.start", "status": "started", "count": 1},
        {"action": "docker.stop", "status": "skipped", "count": 1},
    ]
    assert week["guard"] == [{"kind": "redacted", "count": 1}]
    assert month["actions"] == [
        {"action": "docker.restart", "status": "started", "count": 1},
        {"action": "docker.start", "status": "started", "count": 1},
        {"action": "docker.stop", "status": "skipped", "count": 2},
    ]
    assert month["guard"] == [
        {"kind": "redacted", "count": 1},
        {"kind": "refused_command", "count": 1},
    ]


@pytest.fixture
def zone(monkeypatch):
    """Sets the process's time zone, which SQLite's `localtime` reads, and puts it back."""

    def set_zone(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_zone
    monkeypatch.undo()
    time.tzset()


def seed_at(world: World, service: str, stamp: datetime, spent: bool) -> str:
    """An incident opened at `stamp` with one action started and one guard entry at `stamp`; the
    included ones restart and are redacted, the left-out ones stop and are refused."""
    incident = world.open(service, at=stamp)
    log, guard = world.parts.graph_deps.action_log, world.parts.guard_log
    log.clock = guard.clock = lambda: stamp
    action = "docker.restart" if spent else "docker.stop"
    world.call(log.start, incident, "k", 0, PlanStep(action=action, host_id="a", target="web-1"))
    guard.record(incident, "redacted" if spent else "refused_command", "x")
    if spent:
        world.spend(incident, "m", 1.0)
    return incident


@pytest.mark.parametrize(
    ("name", "left_out", "kept"),
    [
        # UTC+10: 20:00Z on the 12th is already the 13th, 13:00Z is 23:00 on the 12th.
        (
            "Etc/GMT-10",
            datetime(2026, 11, 12, 13, 0, tzinfo=UTC),
            datetime(2026, 11, 12, 20, 0, tzinfo=UTC),
        ),
        # UTC-8: 05:00Z on the 13th is 21:00 on the 12th, 09:00Z is 01:00 on the 13th.
        (
            "Etc/GMT+8",
            datetime(2026, 11, 13, 5, 0, tzinfo=UTC),
            datetime(2026, 11, 13, 9, 0, tzinfo=UTC),
        ),
    ],
)
def test_incidents_actions_and_guard_entries_fall_on_local_days_not_utc_days(
    world, zone, name, left_out, kept
):
    zone(name)
    assert left_out.date() == kept.date()  # the same UTC day, on two local ones
    seed_at(world, "a/before", left_out, spent=False)
    included = seed_at(world, "a/after", kept, spent=True)

    body = world.usage(1)  # the local day of `NOW`, 2026-11-13

    assert body["incidents"]["count"] == 1
    assert body["incidents"]["most_expensive"]["incident_id"] == included
    assert body["actions"] == [{"action": "docker.restart", "status": "started", "count": 1}]
    assert body["guard"] == [{"kind": "redacted", "count": 1}]


def test_a_day_that_is_not_a_choice_is_422(world):
    for days in ("0", "2", "365", "x"):
        assert world.client.get("/api/usage", params={"days": days}).status_code == 422


def test_signed_out_requests_get_401(client):
    response = client.get("/api/usage")
    assert response.status_code == 401
    assert response.json() == {"detail": "not authenticated"}
