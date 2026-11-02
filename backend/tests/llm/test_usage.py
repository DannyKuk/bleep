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


async def test_check_budget_daily_cost(ledger):
    await ledger.add("i", "m", 1, 1, 1.0)
    with pytest.raises(BudgetExceeded) as caught:
        await check_budget(ledger, "other", Budgets())
    assert caught.value.kind == "daily_cost"


async def test_check_budget_incident_tokens(ledger):
    await ledger.add("i", "m", 100_000, 50_000, 0.01)
    with pytest.raises(BudgetExceeded) as caught:
        await check_budget(ledger, "i", Budgets())
    assert caught.value.kind == "incident_tokens"
    await check_budget(ledger, "other", Budgets())


async def test_check_budget_passes_under_limits(ledger):
    await ledger.add("i", "m", 10, 10, 0.5)
    await check_budget(ledger, "i", Budgets())
