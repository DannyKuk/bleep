"""The Usage page's numbers: one read endpoint for one range (#547). See docs/design/app.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BeforeValidator

from bleep.api.deps import get_parts
from bleep.llm.usage_report import usage_report
from bleep.schemas.usage import UsageRange, UsageView
from bleep.wiring import AppParts

router = APIRouter(prefix="/usage", tags=["usage"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("")
async def usage(
    parts: Parts, days: Annotated[UsageRange, BeforeValidator(int), Query()] = 30
) -> UsageView:
    """Model calls, tokens and cost per model and provider and per day, how incidents ended, what
    the action log and the guard log hold, and today against the daily caps, over the last `days`
    local days (1, 7, 30 or 90; 1 is today)."""
    return await usage_report(
        parts.graph_deps.ledger, parts.settings_store.budgets(), days, parts.guard_log.counts_since
    )
