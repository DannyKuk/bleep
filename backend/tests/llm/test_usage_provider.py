"""Each ledger row records the provider its call went through (#547)."""

from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from bleep.llm.factory import PROVIDER_METADATA, cached, chat_model
from bleep.llm.usage import CostLedger, UsageTracker
from bleep.schemas.settings import ModelSettings
from bleep.store.db import Database
from fakes import FakeChatModel


@pytest.fixture
async def ledger(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return CostLedger(db, clock=lambda: datetime(2026, 11, 13, 12, 0))


async def providers(ledger: CostLedger) -> list[str | None]:
    conn = await ledger.db.connect()
    try:
        async with conn.execute("SELECT provider FROM usage ORDER BY id") as cursor:
            return [row["provider"] for row in await cursor.fetchall()]
    finally:
        await conn.close()


def reply() -> AIMessage:
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        response_metadata={"model_name": "m"},
    )


class Answer(BaseModel):
    text: str = ""


@pytest.mark.parametrize("provider", ["anthropic", "openrouter", "local"])
async def test_a_call_is_recorded_with_the_provider_its_model_names(ledger, provider):
    model = FakeChatModel(script=[reply()], metadata={PROVIDER_METADATA: provider})
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})
    assert await providers(ledger) == [provider]


async def test_the_provider_survives_tools_structured_output_and_caching(ledger):
    base = FakeChatModel(script=[reply(), reply(), reply()], metadata={PROVIDER_METADATA: "local"})
    tracker = UsageTracker("i", ledger)
    config = {"callbacks": [tracker]}
    await base.bind_tools([Answer]).ainvoke([HumanMessage("a")], config=config)
    await cached(base).ainvoke([HumanMessage("b")], config=config)
    await base.ainvoke([HumanMessage("c")], config=config)
    assert await providers(ledger) == ["local"] * 3
    assert tracker._providers == {}  # nothing is kept after a call ends


async def test_a_model_without_a_provider_is_recorded_without_one(ledger):
    await FakeChatModel(script=[reply()]).ainvoke(
        [HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]}
    )
    assert await providers(ledger) == [None]


async def test_each_call_of_one_tracker_keeps_its_own_provider(ledger):
    tracker = UsageTracker("i", ledger)
    config = {"callbacks": [tracker]}
    for provider in ("anthropic", "local"):
        model = FakeChatModel(script=[reply()], metadata={PROVIDER_METADATA: provider})
        await model.ainvoke([HumanMessage("a")], config=config)
    assert await providers(ledger) == ["anthropic", "local"]


def test_the_factory_names_the_provider_on_every_model_it_builds(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for provider in ("openrouter", "anthropic", "local"):
        settings = ModelSettings(provider=provider)
        for role in ("main", "cheap"):
            model = chat_model(settings, role, timeout_s=30)
            assert model.metadata[PROVIDER_METADATA] == provider
            assert cached(model).metadata[PROVIDER_METADATA] == provider
