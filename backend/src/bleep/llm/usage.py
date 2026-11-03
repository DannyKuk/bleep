"""Token and cost accounting per incident, with a daily cap."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import ensure_config, merge_configs

from bleep.schemas.settings import Budgets
from bleep.store.db import Database

log = logging.getLogger(__name__)

# USD per million tokens (input, output). OpenRouter reports the real cost of each call, so
# this table is only the fallback; local models are free. Unknown cloud models cost None.
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-6-luna": (0.10, 0.50),  # measured on OpenRouter, see molecula chat-model notes
    "openai/gpt-oss-20b": (0.0, 0.0),
}
_warned: set[str] = set()


class BudgetExceeded(Exception):
    def __init__(self, kind: Literal["incident_tokens", "daily_cost"]) -> None:
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


class CostLedger:
    def __init__(self, db: Database, clock: Callable[[], datetime] = datetime.now) -> None:
        """`clock` returns the machine's local time; tests inject one to cross midnight."""
        self.db = db
        self.clock = clock

    async def add(
        self, incident_id: str, model: str, tokens_in: int, tokens_out: int, usd: float | None
    ) -> None:
        now = self.clock()
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT INTO usage"
                " (incident_id, model, tokens_in, tokens_out, usd, day, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    incident_id,
                    model,
                    tokens_in,
                    tokens_out,
                    usd,
                    now.date().isoformat(),
                    now.isoformat(),
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

    async def spent_today(self) -> float:
        """Dollars recorded since local midnight."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT COALESCE(SUM(usd), 0) AS usd FROM usage WHERE day = ?",
                (self.clock().date().isoformat(),),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return float(row["usd"])


def _cost(model: str, metadata: dict[str, Any], tokens_in: int, tokens_out: int) -> float | None:
    if isinstance(metadata.get("cost"), int | float):
        return float(metadata["cost"])
    if model in PRICES:
        price_in, price_out = PRICES[model]
        return (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    if model not in _warned:
        _warned.add(model)
        log.warning("no price known for model %s; recording tokens without cost", model)
    return None


class UsageTracker(AsyncCallbackHandler):
    """Pass in `config={"callbacks": [tracker]}`; records each model call to the ledger."""

    # LangChain swallows callback errors by default; a failed write must not let spending go dark.
    raise_error = True

    def __init__(self, incident_id: str, ledger: CostLedger) -> None:
        self.incident_id = incident_id
        self.ledger = ledger

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
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
                usd = _cost(model, metadata, tokens_in, tokens_out)
                await self.ledger.add(self.incident_id, model, tokens_in, tokens_out, usd)


async def check_budget(ledger: CostLedger, incident_id: str, budgets: Budgets) -> None:
    """Call before each model call; raises when the day's cap or the incident's tokens are spent."""
    if await ledger.spent_today() >= budgets.daily_cost_usd:
        raise BudgetExceeded("daily_cost")
    if (await ledger.incident_total(incident_id)).tokens >= budgets.incident_tokens:
        raise BudgetExceeded("incident_tokens")


def tracked(tracker: UsageTracker) -> RunnableConfig:
    """The config for a model call inside a node: the node's own callbacks (a tracer's among
    them) plus `tracker`. `config={"callbacks": [tracker]}` alone would replace them, and the
    call would drop out of the incident's trace."""
    return merge_configs(ensure_config(), {"callbacks": [tracker]})
