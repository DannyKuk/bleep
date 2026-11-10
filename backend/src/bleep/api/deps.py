from fastapi import HTTPException, Request, Response

from bleep.api.cookie import COOKIE_NAME, set_session_cookie
from bleep.config import Settings
from bleep.incidents.expectations import Expectations
from bleep.incidents.runner import IncidentRunner
from bleep.talk.actions import Actions
from bleep.wiring import AppParts


def get_parts(request: Request) -> AppParts:
    return request.app.state.parts


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_runner(request: Request) -> IncidentRunner:
    """The runner the lifespan started; it exists only while the app runs."""
    return request.app.state.runner


def get_expectations(request: Request) -> Expectations:
    """ "This is expected", built with the runner by the lifespan."""
    return request.app.state.expectations


def get_actions(request: Request) -> Actions:
    """What the dashboard's chat panel runs a confirmed offer with: the code Telegram's
    buttons use, as the dashboard."""
    parts = get_parts(request)
    return Actions(
        parts.approvals,
        get_runner(request),
        "dashboard",
        parts.quiet.clock,
        incidents=parts.incidents,
        quiet=parts.quiet,
        expectations=get_expectations(request),
        zone=parts.settings_store.zone,
    )


async def require_session(request: Request, response: Response) -> str:
    """401 unless the request carries a live session cookie; returns the raw token."""
    token = request.cookies.get(COOKIE_NAME)
    check = await get_parts(request).sessions.check(token) if token else None
    if token is None or check is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if check.refreshed:  # the expiry slid, so the browser's copy slides with it
        set_session_cookie(response, token, get_settings(request))
    return token
