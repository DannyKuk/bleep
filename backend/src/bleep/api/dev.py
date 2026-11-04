"""The dev panel's routes: prompt overrides, cost per incident and per day, and the guard log.

`create_app` includes this router only when `BLEEP_DEV_PANEL=true`, behind the session like the
rest of `/api`; otherwise every path here is a 404. See docs/design/dev-panel.md.

A prompt's read carries its `version` (in the body and, for one prompt, as the `ETag`), and a save
or reset sends it back in `If-Match`, as `PUT /api/settings` does: 428 without it, 409 with the
version now when the prompt has changed since.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from bleep import prompts
from bleep.api.deps import get_parts
from bleep.api.settings import parse_body, stale_response, version_of
from bleep.guard.log import GuardKind
from bleep.incidents.prompt_overrides import PromptRefused, PromptStale, UnknownPrompt
from bleep.llm.usage import UsageSum
from bleep.schemas.dev import (
    CostView,
    DayCost,
    DevStatus,
    GuardEntryView,
    IncidentCost,
    PromptOverrideBody,
    PromptView,
)
from bleep.wiring import AppParts

router = APIRouter(prefix="/dev", tags=["dev"])

Parts = Annotated[AppParts, Depends(get_parts)]
MAX_ROWS = 500


@router.get("")
async def dev_status() -> DevStatus:
    return DevStatus()


def _prompt(parts: AppParts, name: str) -> PromptView:
    text = prompts.file_text(name)
    override = parts.prompt_overrides.overrides().get(name)
    return PromptView(
        name=name,
        placeholders=sorted(prompts.placeholders(text)),
        file_text=text,
        override=override.text if override else None,
        overridden_at=override.updated_at if override else None,
        version=parts.prompt_overrides.version(name),
    )


def _version(if_match: str | None) -> str:
    if if_match is None or not if_match.strip():
        raise HTTPException(428, "send the prompt's version you read in If-Match")
    return version_of(if_match)


def _stale(stale: PromptStale) -> JSONResponse:
    return stale_response(stale.current, "the prompt changed since this version was read")


def _answer(parts: AppParts, name: str, response: Response) -> PromptView:
    view = _prompt(parts, name)
    response.headers["ETag"] = f'"{view.version}"'
    return view


@router.get("/prompts")
async def list_prompts(parts: Parts) -> list[PromptView]:
    """Every prompt Bleep loads in the app, with its file text and its override."""
    return [_prompt(parts, name) for name in prompts.overridable_names()]


@router.put("/prompts/{name:path}", response_model=PromptView)
async def set_prompt(
    name: str,
    body: Annotated[Any, Body()],
    parts: Parts,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> PromptView | JSONResponse:
    """Save an override; 422 unless it passes the loader's checks and holds no secret.
    `If-Match` holds the prompt's `version` the text was written from."""
    if name not in prompts.overridable_names():
        raise HTTPException(404, "no such prompt")
    version = _version(if_match)
    override = parse_body(body, PromptOverrideBody)
    try:
        await parts.prompt_overrides.set(name, override.text, version=version)
    except PromptStale as stale:
        return _stale(stale)
    except UnknownPrompt:
        raise HTTPException(404, "no such prompt") from None
    except PromptRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    return _answer(parts, name, response)


@router.delete("/prompts/{name:path}", response_model=PromptView)
async def reset_prompt(
    name: str,
    parts: Parts,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> PromptView | JSONResponse:
    """Delete the override, so the prompt is read from its file again. `If-Match` holds the
    prompt's `version` the reset was asked from."""
    if name not in prompts.overridable_names():
        raise HTTPException(404, "no such prompt")
    try:
        await parts.prompt_overrides.reset(name, version=_version(if_match))
    except PromptStale as stale:
        return _stale(stale)
    except UnknownPrompt:
        raise HTTPException(404, "no such prompt") from None
    return _answer(parts, name, response)


def _usage(row: UsageSum) -> dict[str, Any]:
    return {
        "calls": row.calls,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "usd": row.usd,
        "unpriced": row.unpriced,
    }


@router.get("/costs")
async def costs(
    parts: Parts,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 100,
) -> CostView:
    """Tokens and dollars from the usage ledger, per incident (the latest first, with the link to
    its trace) and per local day (today first)."""
    ledger = parts.graph_deps.ledger
    rows = []
    for row in await ledger.by_incident(limit):
        cost = IncidentCost(incident_id=row.key, **_usage(row))
        incident = await parts.incidents.get(row.key)
        if incident is not None:
            cost.title, cost.status = incident.title, incident.status
            cost.opened_at, cost.trace_url = incident.opened_at, incident.trace_url
        rows.append(cost)
    return CostView(
        incidents=rows,
        days=[DayCost(day=row.key, **_usage(row)) for row in await ledger.by_day(days)],
        daily_cap_usd=parts.settings_store.budgets().daily_cost_usd,
        spent_today_usd=await ledger.spent_today(),
    )


@router.get("/guard-log")
async def guard_log(
    parts: Parts,
    kind: GuardKind | None = None,
    incident_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 200,
) -> list[GuardEntryView]:
    """What the guard refused, blocked or changed, newest first."""
    entries = await parts.guard_log.recent(incident_id, kind, limit)
    return [
        GuardEntryView(id=e.id, incident_id=e.incident_id, kind=e.kind, detail=e.detail, at=e.at)
        for e in entries
    ]
