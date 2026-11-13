from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from bleep.llm import usage
from bleep.llm.usage import BudgetExceeded, CostLedger, UsageTracker, check_budget
from bleep.schemas.settings import Budgets
from bleep.store.db import Database
from fakes import FakeChatModel


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def ledger(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return CostLedger(db, clock=Clock(datetime(2026, 11, 2, 12, 0)))


def reply(text="ok", tin=100, tout=20, **metadata):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout},
        response_metadata=metadata,
    )


async def test_tracker_records_tokens_per_incident(ledger):
    model = FakeChatModel(script=[reply(tin=100, tout=20), reply(tin=50, tout=5), reply()])
    one = UsageTracker("inc-1", ledger)
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [one]})
    await model.ainvoke([HumanMessage("b")], config={"callbacks": [one]})
    await model.ainvoke([HumanMessage("c")], config={"callbacks": [UsageTracker("inc-2", ledger)]})
    total = await ledger.incident_total("inc-1")
    assert (total.tokens_in, total.tokens_out) == (150, 25)
    assert total.tokens == 175
    assert (await ledger.incident_total("inc-2")).tokens == 120


async def test_tracker_uses_openrouter_cost_when_present(ledger):
    model = FakeChatModel(script=[reply(model_name="openai/gpt-6-luna", cost=0.0123)])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).usd == pytest.approx(0.0123)
    assert await ledger.spent_today() == pytest.approx(0.0123)


async def test_local_model_costs_nothing(ledger):
    model = FakeChatModel(script=[reply(model_name="openai/gpt-oss-20b")])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).usd == 0


async def test_unknown_model_records_tokens_without_cost_and_warns_once(ledger, caplog):
    usage._warned.clear()
    model = FakeChatModel(script=[reply(model_name="mystery/m"), reply(model_name="mystery/m")])
    tracker = UsageTracker("i", ledger)
    for _ in range(2):
        await model.ainvoke([HumanMessage("a")], config={"callbacks": [tracker]})
    total = await ledger.incident_total("i")
    assert total.tokens == 240
    assert total.usd is None
    assert [r.message for r in caplog.records if "mystery/m" in r.message].__len__() == 1


async def test_spent_today_resets_at_local_midnight(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    clock = Clock(datetime(2026, 11, 2, 23, 59))
    ledger = CostLedger(db, clock=clock)
    await ledger.add("i", "m", 10, 10, 0.4)
    assert await ledger.spent_today() == pytest.approx(0.4)
    clock.now = datetime(2026, 11, 3, 0, 1)
    assert await ledger.spent_today() == 0
    await ledger.add("i", "m", 10, 10, 0.1)
    assert await ledger.spent_today() == pytest.approx(0.1)
    assert (await ledger.incident_total("i")).usd == pytest.approx(0.5)


async def test_chat_answers_have_their_own_daily_cap_and_never_count_against_the_incidents(
    ledger,
):
    await ledger.add(usage.CHAT_USAGE, "m", 10, 10, 0.3)
    await ledger.add("inc_1", "m", 10, 10, 0.5)

    assert await ledger.spent_today() == pytest.approx(0.5)
    assert await ledger.spent_today(usage.CHAT_USAGE) == pytest.approx(0.3)
    assert await ledger.spent_today(chat=True) == pytest.approx(0.8)
    await usage.check_daily_cap(ledger, Budgets(daily_cost_usd=0.6))  # chat's 0.3 not counted
    with pytest.raises(BudgetExceeded) as raised:
        await usage.check_chat_cap(ledger, Budgets(chat_daily_cost_usd=0.2))
    assert raised.value.kind == "chat_daily_cost"
    await usage.check_chat_cap(ledger, Budgets(chat_daily_cost_usd=0.5))


async def test_check_budget_daily_cost(ledger):
    await ledger.add("i", "m", 1, 1, 1.0)
    with pytest.raises(BudgetExceeded) as caught:
        await check_budget(ledger, "other", Budgets())
    assert caught.value.kind == "daily_cost"


async def test_check_budget_incident_tokens(ledger):
    await ledger.add("i", "m", 200_000, 100_000, 0.01)
    with pytest.raises(BudgetExceeded) as caught:
        await check_budget(ledger, "i", Budgets())
    assert caught.value.kind == "incident_tokens"
    await check_budget(ledger, "other", Budgets())


async def test_an_incident_the_size_of_a_claude_investigation_is_not_stopped(ledger):
    await ledger.add("i", "claude-sonnet-5-5", 156_562, 5_269, None)
    await check_budget(ledger, "i", Budgets())


async def test_check_budget_passes_under_limits(ledger):
    await ledger.add("i", "m", 10, 10, 0.5)
    await check_budget(ledger, "i", Budgets())


async def test_default_cloud_model_has_a_fallback_price(ledger):
    model = FakeChatModel(
        script=[reply(model_name="openai/gpt-6-luna", tin=1_000_000, tout=1_000_000)]
    )
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).usd == pytest.approx(0.60)


async def test_ledger_failure_surfaces_instead_of_failing_open(ledger):
    class Broken(CostLedger):
        async def add(self, *args, **kwargs):
            raise RuntimeError("disk full")

    broken = Broken(ledger.db)
    model = FakeChatModel(script=[reply()])
    with pytest.raises(RuntimeError, match="disk full"):
        await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", broken)]})


async def three_incidents_over_two_days(tmp_path) -> CostLedger:
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    clock = Clock(datetime(2026, 11, 2, 23, 0))
    ledger = CostLedger(db, clock=clock)
    await ledger.add("inc-1", "openai/gpt-6-luna", 1000, 100, 0.25)
    await ledger.add("inc-2", "openai/gpt-6-luna", 2000, 200, 0.5)
    clock.now = datetime(2026, 11, 3, 9, 0)
    await ledger.add("inc-1", "openai/gpt-6-luna", 500, 50, 0.125)
    await ledger.add("inc-3", "mystery/m", 300, 30, None)
    await ledger.add("inc-3", "openai/gpt-oss-20b", 10, 1, 0.0)
    return ledger


async def test_cost_per_incident_sums_the_ledger_rows_newest_first(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)

    rows = await ledger.by_incident()

    assert [(r.key, r.calls, r.tokens_in, r.tokens_out) for r in rows] == [
        ("inc-3", 2, 310, 31),
        ("inc-1", 2, 1500, 150),
        ("inc-2", 1, 2000, 200),
    ]
    assert [r.usd for r in rows] == [pytest.approx(0.0), pytest.approx(0.375), pytest.approx(0.5)]
    assert [r.unpriced for r in rows] == [1, 0, 0]
    for row in rows:
        total = await ledger.incident_total(row.key)
        assert (row.tokens_in, row.tokens_out, row.usd) == (
            total.tokens_in,
            total.tokens_out,
            total.usd,
        )


async def test_cost_per_day_sums_the_ledger_rows_and_today_matches_the_cap_check(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)

    days = await ledger.by_day(days=30)

    assert [(d.key, d.calls, d.tokens_in, d.tokens_out, d.unpriced) for d in days] == [
        ("2026-11-03", 3, 810, 81, 1),
        ("2026-11-02", 2, 3000, 300, 0),
    ]
    assert days[0].usd == pytest.approx(await ledger.spent_today())
    assert days[1].usd == pytest.approx(0.75)
    incidents = await ledger.by_incident()
    assert sum(d.tokens_in for d in days) == sum(r.tokens_in for r in incidents)
    assert sum(d.usd or 0 for d in days) == pytest.approx(sum(r.usd or 0 for r in incidents))


async def test_cost_per_day_keeps_only_the_days_asked_for(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)
    assert [d.key for d in await ledger.by_day(days=1)] == ["2026-11-03"]


async def test_cost_per_model_sums_each_models_calls_and_counts_the_unpriced(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)

    models = await ledger.by_model(days=30)

    assert [(m.key, m.calls, m.tokens_in, m.tokens_out, m.unpriced) for m in models] == [
        ("openai/gpt-6-luna", 3, 3500, 350, 0),
        ("mystery/m", 1, 300, 30, 1),
        ("openai/gpt-oss-20b", 1, 10, 1, 0),
    ]
    assert models[0].usd == pytest.approx(0.875)
    assert models[1].usd is None
    assert sum(m.calls for m in models) == sum(r.calls for r in await ledger.by_incident())


async def test_cost_per_model_for_today_leaves_out_earlier_days(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)

    today = await ledger.by_model(days=1)

    assert [(m.key, m.calls) for m in today] == [
        ("mystery/m", 1),
        ("openai/gpt-6-luna", 1),
        ("openai/gpt-oss-20b", 1),
    ]


async def test_the_models_of_each_incident_come_in_one_query_first_used_first(tmp_path):
    ledger = await three_incidents_over_two_days(tmp_path)

    models = await ledger.models_of(["inc-1", "inc-3", "nobody"])

    assert models == {"inc-1": ["openai/gpt-6-luna"], "inc-3": ["mystery/m", "openai/gpt-oss-20b"]}
    assert await ledger.models_of([]) == {}


async def test_a_day_of_unpriced_calls_only_has_no_cost(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    ledger = CostLedger(db, clock=Clock(datetime(2026, 11, 3, 9, 0)))
    await ledger.add("inc-1", "mystery/m", 10, 1, None)
    [day] = await ledger.by_day()
    [incident] = await ledger.by_incident()
    assert (day.usd, day.unpriced, incident.usd, incident.unpriced) == (None, 1, None, 1)


def local_reply(completion_tokens, reasoning_tokens):
    """What the OpenAI-protocol client makes of a local server's reply that carries usage."""
    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(model="local", api_key="none", base_url="http://localhost:1234/v1")
    body = {
        "id": "x",
        "model": "local",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }
    return chat._create_chat_result(body).generations[0].message


async def test_local_reply_with_openai_usage_is_recorded(ledger):
    model = FakeChatModel(script=[local_reply(20, 8)])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).tokens == 120


async def test_reasoning_tokens_the_server_reports_beside_the_completion_are_counted(ledger):
    model = FakeChatModel(script=[local_reply(12, 30)])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).tokens_out == 42


async def test_reasoning_tokens_inside_the_completion_are_not_counted_twice(ledger):
    model = FakeChatModel(script=[local_reply(40, 30)])
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert (await ledger.incident_total("i")).tokens_out == 40
