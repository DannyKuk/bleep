"""Token and cost accounting per incident, with a daily cap."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import ensure_config, merge_configs

from bleep.schemas.settings import Budgets
from bleep.store.db import Database

log = logging.getLogger(__name__)

# USD per million tokens (input, output). OpenRouter reports the real cost of each call, so
# for it this table is only the fallback; the Anthropic API reports no cost, so the Claude models
# are priced here (Anthropic's pricing page, platform.claude.com/docs/en/about-claude/pricing,
# read 2026-11-12); local models are free. Unknown cloud models cost None.
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-6-luna": (0.10, 0.50),  # measured on OpenRouter, see molecula chat-model notes
    "openai/gpt-oss-20b": (0.0, 0.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.0, 20.0),
}
# Claude Haiku 5.5 charges more for a call whose prompt is over 100,000 tokens: the price of the
# whole call, not of the tokens past the line.
LONG_PROMPT_PRICES: dict[str, tuple[int, tuple[float, float]]] = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
}
# What a cache read and a cache write cost as a multiple of the model's input price (the same
# multiples apply to Haiku 5.5's long-prompt prices): Sonnet 5.5 and Opus 5.5 read at 5%, Haiku 5.5
# at 10%, and a 5-minute write is 1.25x for all of them (same pricing page). Bleep asks for the
# 5-minute entry only. A model in `PRICES` but not here has its cache tokens priced as input.
CACHE_RATES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (0.05, 1.25),
    "claude-haiku-5-5": (0.10, 1.25),
    "claude-opus-5-5": (0.05, 1.25),
}
_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")
_warned: set[str] = set()


class BudgetExceeded(Exception):
    def __init__(self, kind: Literal["incident_tokens", "daily_cost", "chat_daily_cost"]) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class IncidentTotal:
    tokens_in: int
    tokens_out: int
    usd: float | None

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class UsageSum:
    """The ledger's rows for one incident or one day, summed. `usd` sums the calls whose cost is
    known and is None when none is; `unpriced` counts the calls without a cost."""

    key: str
    calls: int
    tokens_in: int
    tokens_out: int
    usd: float | None
    unpriced: int


_SUMS = (
    "COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,"
    " SUM(usd) AS usd, COUNT(*) - COUNT(usd) AS unpriced"
)


# The ledger's key for chat answers (`talk`), which have no incident and a daily cap of their own
# (`Budgets.chat_daily_cost_usd`): what a chat spends never stops an investigation.
CHAT_USAGE = "talk"


class CostLedger:
    def __init__(self, db: Database, clock: Callable[[], datetime] = datetime.now) -> None:
        """`clock` returns the machine's local time; tests inject one to cross midnight."""
        self.db = db
        self.clock = clock

    async def add(
        self,
        incident_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        usd: float | None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """`tokens_in` counts every input token; the cache reads and writes are parts of it."""
        now = self.clock()
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT INTO usage"
                " (incident_id, model, tokens_in, tokens_out, usd, day, created_at,"
                " cache_read_tokens, cache_write_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    incident_id,
                    model,
                    tokens_in,
                    tokens_out,
                    usd,
                    now.date().isoformat(),
                    now.isoformat(),
                    cache_read_tokens,
                    cache_write_tokens,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def incident_total(self, incident_id: str) -> IncidentTotal:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT COALESCE(SUM(tokens_in), 0) AS i, COALESCE(SUM(tokens_out), 0) AS o,"
                " SUM(usd) AS usd, COUNT(*) AS n, COUNT(usd) AS priced"
                " FROM usage WHERE incident_id = ?",
                (incident_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        usd = row["usd"] if row["priced"] else None
        return IncidentTotal(row["i"], row["o"], usd)

    async def spent_today(self, key: str | None = None, chat: bool = False) -> float:
        """Dollars recorded since local midnight: under `key` alone when given, else for the
        incidents and the recovery lines, without chat answers (`CHAT_USAGE`) unless `chat`."""
        where, args = "day = ?", [self.clock().date().isoformat()]
        if key is not None:
            where, args = where + " AND incident_id = ?", [*args, key]
        elif not chat:
            where, args = where + " AND incident_id != ?", [*args, CHAT_USAGE]
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT COALESCE(SUM(usd), 0) AS usd FROM usage WHERE {where}", args
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return float(row["usd"])

    async def by_incident(self, limit: int = 100) -> list[UsageSum]:
        """Each incident's sums, the one with the latest model call first."""
        return await self._sums(
            f"SELECT incident_id AS key, {_SUMS} FROM usage"
            " GROUP BY incident_id ORDER BY MAX(id) DESC LIMIT ?",
            (limit,),
        )

    async def by_day(self, days: int = 30) -> list[UsageSum]:
        """Each local day's sums over the last `days` days, today first; days without a model
        call are left out."""
        first = self.clock().date() - timedelta(days=days - 1)
        return await self._sums(
            f"SELECT day AS key, {_SUMS} FROM usage WHERE day >= ? GROUP BY day ORDER BY day DESC",
            (first.isoformat(),),
        )

    async def _sums(self, query: str, args: tuple[object, ...]) -> list[UsageSum]:
        conn = await self.db.connect()
        try:
            async with conn.execute(query, args) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [
            UsageSum(r["key"], r["calls"], r["tokens_in"], r["tokens_out"], r["usd"], r["unpriced"])
            for r in rows
        ]


def _cost(
    model: str,
    metadata: dict[str, Any],
    tokens_in: int,
    tokens_out: int,
    *,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float | None:
    """`tokens_in` counts the cache reads and writes too, as the providers' prompt sizes do."""
    if isinstance(metadata.get("cost"), int | float):
        return float(metadata["cost"])
    priced = _SNAPSHOT_SUFFIX.sub("", model)  # the API may answer with a dated id of the alias
    if priced in PRICES:
        price_in, price_out = PRICES[priced]
        threshold, long_prices = LONG_PROMPT_PRICES.get(priced, (None, None))
        if threshold is not None and long_prices is not None and tokens_in > threshold:
            price_in, price_out = long_prices
        read_rate, write_rate = CACHE_RATES.get(priced, (1.0, 1.0))
        plain = max(tokens_in - cache_read - cache_write, 0)
        return (
            (plain + cache_read * read_rate + cache_write * write_rate) * price_in
            + tokens_out * price_out
        ) / 1_000_000
    if model not in _warned:
        _warned.add(model)
        log.warning("no price known for model %s; recording tokens without cost", model)
    return None


@dataclass(frozen=True)
class CallCost:
    model: str
    tokens_in: int
    tokens_out: int
    usd: float | None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _cache_tokens(tokens_in: int, details: dict[str, Any]) -> tuple[int, int]:
    """The cache reads and writes inside `tokens_in`, from what LangChain reports. For Claude
    `input_tokens` already adds both to the plain input, so they are parts of it, not extra.
    A write is reported under `cache_creation`, or split by lifetime when the API says it."""
    read = details.get("cache_read") or 0
    write = sum(
        details.get(key) or 0
        for key in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
    )
    read = min(read, tokens_in)
    return read, min(write, tokens_in - read)


def call_costs(response: LLMResult) -> list[CallCost]:
    """What each generation of a model call cost, for those that report their tokens."""
    costs: list[CallCost] = []
    for generations in response.generations:
        for generation in generations:
            if not isinstance(generation, ChatGeneration):
                continue
            message = generation.message
            tokens = getattr(message, "usage_metadata", None)
            if not tokens:
                continue
            metadata = message.response_metadata or {}
            model = str(metadata.get("model_name") or metadata.get("model") or "unknown")
            tokens_in, tokens_out = tokens["input_tokens"], tokens["output_tokens"]
            reasoning = (tokens.get("output_token_details") or {}).get("reasoning") or 0
            if reasoning > tokens_out:
                # Some servers report reasoning beside the completion, not inside it; more
                # reasoning than output cannot be a part of it.
                tokens_out += reasoning
            cache_read, cache_write = _cache_tokens(
                tokens_in, tokens.get("input_token_details") or {}
            )
            usd = _cost(
                model,
                metadata,
                tokens_in,
                tokens_out,
                cache_read=cache_read,
                cache_write=cache_write,
            )
            costs.append(CallCost(model, tokens_in, tokens_out, usd, cache_read, cache_write))
    return costs


class UsageTracker(AsyncCallbackHandler):
    """Pass in `config={"callbacks": [tracker]}`; records each model call to the ledger."""

    # LangChain swallows callback errors by default; a failed write must not let spending go dark.
    raise_error = True

    def __init__(self, incident_id: str, ledger: CostLedger) -> None:
        self.incident_id = incident_id
        self.ledger = ledger

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for call in call_costs(response):
            await self.ledger.add(
                self.incident_id,
                call.model,
                call.tokens_in,
                call.tokens_out,
                call.usd,
                call.cache_read_tokens,
                call.cache_write_tokens,
            )


class CallCosts(AsyncCallbackHandler):
    """Keeps what each model call it sees cost, so a log can show it beside the call."""

    def __init__(self) -> None:
        self.calls: list[CallCost] = []

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        self.calls.extend(call_costs(response))


async def check_daily_cap(ledger: CostLedger, budgets: Budgets) -> None:
    """Raises when the day's cap is spent; for a model call that belongs to no incident. Chat
    answers are not counted: they have their own cap (`check_chat_cap`)."""
    if await ledger.spent_today() >= budgets.daily_cost_usd:
        raise BudgetExceeded("daily_cost")


async def check_chat_cap(ledger: CostLedger, budgets: Budgets) -> None:
    """Raises when the day's chat answers have spent their own cap."""
    if await ledger.spent_today(CHAT_USAGE) >= budgets.chat_daily_cost_usd:
        raise BudgetExceeded("chat_daily_cost")


async def check_budget(ledger: CostLedger, incident_id: str, budgets: Budgets) -> None:
    """Call before each model call; raises when the day's cap or the incident's tokens are spent."""
    await check_daily_cap(ledger, budgets)
    if (await ledger.incident_total(incident_id)).tokens >= budgets.incident_tokens:
        raise BudgetExceeded("incident_tokens")


def tracked(tracker: UsageTracker) -> RunnableConfig:
    """The config for a model call inside a node: the node's own callbacks (a tracer's among
    them) plus `tracker`. `config={"callbacks": [tracker]}` alone would replace them, and the
    call would drop out of the incident's trace."""
    return merge_configs(ensure_config(), {"callbacks": [tracker]})
