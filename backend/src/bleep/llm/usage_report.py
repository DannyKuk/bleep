"""The numbers of the Usage page: one range of the usage ledger, the incidents and their reports,
the action log and the guard log, summed in SQL (#547).

It reads those tables itself because every number must come from one database read path and one
local-day boundary: the ledger's `day` is the machine's local date, and the other tables' UTC
timestamps are put on the same days with SQLite's `localtime`.
"""

from datetime import timedelta
from typing import get_args

import aiosqlite

from bleep.llm.usage import CHAT_USAGE, CostLedger
from bleep.schemas.graph import Outcome, outcome_of
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.schemas.settings import Budgets
from bleep.schemas.usage import (
    ActionCount,
    CostliestIncident,
    DayUsage,
    GuardCount,
    IncidentEndings,
    IncidentUsage,
    ModelUsage,
    TodayCaps,
    UsageProvider,
    UsageRange,
    UsageTotals,
    UsageView,
)

_PROVIDERS: tuple[str, ...] = ("anthropic", "openrouter", "local")


async def usage_report(ledger: CostLedger, budgets: Budgets, days: UsageRange) -> UsageView:
    """Everything in the last `days` local days (1 is today); the caps are always today's."""
    first = (ledger.clock().date() - timedelta(days=days - 1)).isoformat()
    conn = await ledger.db.connect()
    try:
        totals = await _totals(conn, first)
        models = await _models(conn, first)
        per_day = await _per_day(conn, first)
        incidents, endings = await _incidents(conn, first)
        actions = await _actions(conn, first)
        guard = await _guard(conn, first)
    finally:
        await conn.close()
    return UsageView(
        days=days,
        totals=totals,
        models=models,
        per_day=per_day,
        incidents=incidents,
        endings=endings,
        actions=actions,
        guard=guard,
        today=TodayCaps(
            spent_usd=await ledger.spent_today(),
            daily_cap_usd=budgets.daily_cost_usd,
            chat_spent_usd=await ledger.spent_today(CHAT_USAGE),
            chat_daily_cap_usd=budgets.chat_daily_cost_usd,
        ),
    )


async def _totals(conn: aiosqlite.Connection, first: str) -> UsageTotals:
    async with conn.execute(
        "SELECT COUNT(*) AS calls,"
        " COALESCE(SUM(incident_id = ?), 0) AS chat_calls,"
        " COALESCE(SUM(usd), 0) AS usd, COUNT(*) - COUNT(usd) AS unpriced,"
        " COALESCE(SUM(provider = 'local'), 0) AS local_calls,"
        " COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out,"
        " COALESCE(SUM(cache_read_tokens), 0) AS cache_read,"
        " COALESCE(SUM(cache_write_tokens), 0) AS cache_write"
        " FROM usage WHERE day >= ?",
        (CHAT_USAGE, first),
    ) as cursor:
        row = await cursor.fetchone()
    return UsageTotals(
        cost_usd=row["usd"],
        calls=row["calls"],
        incident_calls=row["calls"] - row["chat_calls"],
        chat_calls=row["chat_calls"],
        unpriced_calls=row["unpriced"],
        local_calls=row["local_calls"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cache_read_tokens=row["cache_read"],
        cache_write_tokens=row["cache_write"],
    )


def _provider(stored: str | None) -> UsageProvider:
    return stored if stored in _PROVIDERS else "unknown"  # type: ignore[return-value]


async def _models(conn: aiosqlite.Connection, first: str) -> list[ModelUsage]:
    async with conn.execute(
        "SELECT model, provider, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in,"
        " SUM(tokens_out) AS tokens_out, SUM(cache_read_tokens) AS cache_read,"
        " SUM(cache_write_tokens) AS cache_write, COALESCE(SUM(usd), 0) AS usd,"
        " COUNT(*) - COUNT(usd) AS unpriced"
        " FROM usage WHERE day >= ? GROUP BY model, provider",
        (first,),
    ) as cursor:
        rows = await cursor.fetchall()
    models = [
        ModelUsage(
            model=r["model"],
            provider=_provider(r["provider"]),
            calls=r["calls"],
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            cache_read_tokens=r["cache_read"],
            cache_write_tokens=r["cache_write"],
            cost_usd=r["usd"],
            unpriced_calls=r["unpriced"],
        )
        for r in rows
    ]
    return sorted(models, key=lambda m: (-m.calls, m.model, m.provider))


async def _per_day(conn: aiosqlite.Connection, first: str) -> list[DayUsage]:
    async with conn.execute(
        "SELECT day, model, COALESCE(SUM(usd), 0) AS usd,"
        " COALESCE(SUM(incident_id = ?), 0) AS chat_calls, COUNT(*) AS calls"
        " FROM usage WHERE day >= ? GROUP BY day, model ORDER BY day, model",
        (CHAT_USAGE, first),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        DayUsage(
            day=r["day"],
            model=r["model"],
            cost_usd=r["usd"],
            incident_calls=r["calls"] - r["chat_calls"],
            chat_calls=r["chat_calls"],
        )
        for r in rows
    ]


def _ending(text: str | None) -> Outcome | None:
    """The outcome a report's fixed text stands for; a run that was healthy anyway ended healthy."""
    kind = outcome_of(text) if text is not None else None
    return "healthy" if kind == "healthy_anyway" else kind


async def _incidents(
    conn: aiosqlite.Connection, first: str
) -> tuple[IncidentUsage, IncidentEndings]:
    async with conn.execute(
        "SELECT i.id AS id, i.status AS status, COALESCE(i.short_name, i.title) AS name,"
        " (SELECT SUM(usd) FROM usage u WHERE u.incident_id = i.id) AS usd,"
        " (SELECT json_extract(e.payload, '$.outcome') FROM incident_events e"
        "  WHERE e.incident_id = i.id AND e.kind = 'report' ORDER BY e.seq DESC LIMIT 1) AS outcome"
        " FROM incidents i WHERE date(i.opened_at, 'localtime') >= ? ORDER BY i.opened_at",
        (first,),
    ) as cursor:
        rows = await cursor.fetchall()
    outcomes: dict[Outcome, int] = dict.fromkeys(get_args(Outcome), 0)  # type: ignore[arg-type]
    failed = still_open = other = 0
    for row in rows:
        ended = _ending(row["outcome"])
        if ended is not None:
            outcomes[ended] += 1
        elif row["outcome"] is None and row["status"] == "failed":
            failed += 1
        elif row["outcome"] is None and row["status"] not in TERMINAL_STATUSES:
            still_open += 1
        else:
            other += 1
    priced = [r for r in rows if r["usd"] is not None]
    costliest = max(priced, key=lambda r: r["usd"], default=None)
    return (
        IncidentUsage(
            count=len(rows),
            average_cost_usd=sum(r["usd"] for r in priced) / len(priced) if priced else None,
            most_expensive=CostliestIncident(
                incident_id=costliest["id"], name=costliest["name"], cost_usd=costliest["usd"]
            )
            if costliest
            else None,
        ),
        IncidentEndings(outcomes=outcomes, failed=failed, open=still_open, other=other),
    )


async def _actions(conn: aiosqlite.Connection, first: str) -> list[ActionCount]:
    # A skipped step has no times of its own, so it counts on the day its incident opened.
    async with conn.execute(
        "SELECT a.action AS action, a.status AS status, COUNT(*) AS n FROM action_log a"
        " JOIN incidents i ON i.id = a.incident_id"
        " WHERE date(COALESCE(a.started_at, a.finished_at, i.opened_at), 'localtime') >= ?"
        " GROUP BY a.action, a.status ORDER BY a.action, a.status",
        (first,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [ActionCount(action=r["action"], status=r["status"], count=r["n"]) for r in rows]


async def _guard(conn: aiosqlite.Connection, first: str) -> list[GuardCount]:
    async with conn.execute(
        "SELECT kind, COUNT(*) AS n FROM guard_log WHERE date(at, 'localtime') >= ?"
        " GROUP BY kind ORDER BY kind",
        (first,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [GuardCount(kind=r["kind"], count=r["n"]) for r in rows]
