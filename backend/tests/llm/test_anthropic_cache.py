"""Prompt caching on the Anthropic provider (#529): the request carries a breakpoint, and the
cache reads and writes in a reply are recorded and priced at their own rates. The real
`ChatAnthropic` runs against a mock transport; nothing touches the network."""

import json
from datetime import datetime

import httpx2
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import tool
from pydantic import SecretStr
from test_anthropic import claude, message_body, over_transport

from bleep.llm.factory import chat_model
from bleep.llm.usage import CostLedger, UsageTracker, call_costs
from bleep.schemas.settings import ModelSettings
from bleep.store.db import Database


@pytest.fixture
async def ledger(tmp_path):
    db = Database(tmp_path / "bleep.db")
    await db.migrate()
    return CostLedger(db, clock=lambda: datetime(2026, 11, 13, 12, 0))


def cached_body(model: str, fresh: int, read: int, written: int, tout: int, **extra) -> dict:
    body = message_body(model=model, tin=fresh, tout=tout)
    body["usage"] |= {"cache_read_input_tokens": read, "cache_creation_input_tokens": written}
    body["usage"] |= extra
    return body


async def only_row(ledger: CostLedger) -> dict:
    conn = await ledger.db.connect()
    try:
        async with conn.execute("SELECT * FROM usage") as cursor:
            [row] = await cursor.fetchall()
    finally:
        await conn.close()
    return dict(row)


async def call(body: dict, ledger: CostLedger, role="main") -> None:
    model = chat_model(claude(), role, timeout_s=60)
    over_transport(model, lambda request: httpx2.Response(200, json=body))
    await model.ainvoke([HumanMessage("a")], config={"callbacks": [UsageTracker("i", ledger)]})


async def test_every_claude_request_carries_a_cache_breakpoint_beside_the_tools():
    @tool
    def ping(host: str) -> str:
        """Ping a host."""
        return host

    model = chat_model(claude(), "main", timeout_s=60)
    seen = over_transport(model, lambda request: httpx2.Response(200, json=message_body()))

    await model.bind_tools([ping]).ainvoke([HumanMessage("hello")])

    body = json.loads(seen[0].content)
    assert body["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in body["tools"]] == ["ping"]


@pytest.mark.parametrize("provider", ["openrouter", "local"])
def test_the_other_providers_send_no_cache_control(provider):
    model = chat_model(ModelSettings(provider=provider, openrouter_api_key=SecretStr("k")), "main")
    assert "cache_control" not in (getattr(model, "model_kwargs", None) or {})


async def test_a_cached_reply_is_priced_at_the_cache_read_and_write_rates(ledger):
    body = cached_body("claude-sonnet-5-5", fresh=100, read=10_000, written=2_000, tout=50)

    await call(body, ledger)

    row = await only_row(ledger)
    assert (row["tokens_in"], row["cache_read_tokens"], row["cache_write_tokens"]) == (
        12_100,
        10_000,
        2_000,
    )
    # $2 per million plain, 5% of that to read, 125% to write; $10 per million out
    plain_equivalent = 100 + 10_000 * 0.05 + 2_000 * 1.25
    assert row["usd"] == pytest.approx(plain_equivalent * 2 / 1e6 + 50 * 10 / 1e6)


async def test_a_write_the_api_splits_by_lifetime_is_counted_once(ledger):
    split = {"cache_creation": {"ephemeral_5m_input_tokens": 3_000, "ephemeral_1h_input_tokens": 0}}
    body = cached_body("claude-sonnet-5-5", fresh=0, read=0, written=3_000, tout=0, **split)

    await call(body, ledger)

    row = await only_row(ledger)
    assert (row["tokens_in"], row["cache_write_tokens"]) == (3_000, 3_000)
    assert row["usd"] == pytest.approx(3_000 * 2.5 / 1e6)


async def test_haiku_reads_at_a_tenth_and_its_long_prompt_price_counts_the_cache_too(ledger):
    body = cached_body("claude-haiku-5-5", fresh=20_000, read=90_000, written=0, tout=1_000)

    await call(body, ledger, role="cheap")

    # 110,000 prompt tokens in all: the long-prompt prices, $0.50 in, 10% of it to read, $2.50 out
    expected = (20_000 + 90_000 * 0.10) * 0.5 / 1e6 + 1_000 * 2.5 / 1e6
    assert (await ledger.incident_total("i")).usd == pytest.approx(expected)


async def test_a_reply_without_a_cache_is_priced_as_before(ledger):
    await call(message_body(tin=1_000_000, tout=0), ledger)

    row = await only_row(ledger)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (0, 0)
    assert row["usd"] == pytest.approx(2.0)


async def test_cache_reads_count_in_full_against_the_incident_token_cap(ledger):
    from bleep.llm.usage import BudgetExceeded, check_budget
    from bleep.schemas.settings import Budgets

    body = cached_body("claude-sonnet-5-5", fresh=1_000, read=299_000, written=0, tout=0)
    await call(body, ledger)

    total = await ledger.incident_total("i")
    assert total.tokens_in == 300_000
    with pytest.raises(BudgetExceeded) as spent:
        await check_budget(ledger, "i", Budgets())
    assert spent.value.kind == "incident_tokens"


def other_provider_result(model: str, details: dict, **metadata) -> LLMResult:
    """What an OpenAI-protocol client (OpenRouter, a local server) makes of a reply whose usage
    reports cached prompt tokens."""
    message = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "total_tokens": 1_000_000,
            "input_token_details": details,
        },
        response_metadata={"model_name": model, **metadata},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_the_cost_openrouter_reports_is_taken_as_given_whatever_the_cache_tokens():
    details = {"cache_read": 800_000, "cache_creation": 100_000}
    result = other_provider_result("anthropic/claude-sonnet-5-5", details, cost=0.42)

    [cost] = call_costs(result)

    assert cost.usd == 0.42
    assert (cost.cache_read_tokens, cost.cache_write_tokens) == (800_000, 100_000)


def test_a_priced_model_without_cache_rates_costs_what_it_did_before_caching_was_priced():
    details = {"cache_read": 800_000, "cache_creation": 100_000}

    [cost] = call_costs(other_provider_result("openai/gpt-6-luna", details))

    assert cost.usd == pytest.approx(0.10)  # 1,000,000 tokens at $0.10; cache tokens as plain input
    assert (cost.tokens_in, cost.cache_read_tokens, cost.cache_write_tokens) == (
        1_000_000,
        800_000,
        100_000,
    )
