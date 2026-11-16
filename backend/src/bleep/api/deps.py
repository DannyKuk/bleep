from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from bleep.api.cookie import COOKIE_NAME, set_session_cookie
from bleep.api.errors import Refusal
from bleep.config import Settings
from bleep.incidents.expectations import Expectations
from bleep.incidents.runner import IncidentRunner
from bleep.talk.actions import Actions
from bleep.talk.progress import LookFollower
from bleep.watcher.detector import Pending
from bleep.watcher.manager import WatcherManager
from bleep.wiring import AppParts

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_parts(request: Request) -> AppParts:
    return request.app.state.parts


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_runner(request: Request) -> IncidentRunner:
    """The runner the lifespan started; it exists only while the app runs."""
    return request.app.state.runner


Watching = Callable[[], Awaitable[list[Pending]]]


def get_watching(request: Request) -> Watching | None:
    """What the running watcher waits on before it opens an incident (#358); None before the
    lifespan started it."""
    watcher: WatcherManager | None = getattr(request.app.state, "watcher", None)
    return watcher.watching if watcher is not None else None


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
        looks=request.app.state.looks,
        zone=parts.settings_store.zone,
        language=parts.settings_store.language,
        looked=parts.panel.turns.about,
    )


def get_look_follower(request: Request) -> LookFollower:
    """What the chat panel follows a look it started with (#458), reading the run's checkpoint
    through the runner."""
    parts = get_parts(request)
    return LookFollower(
        parts.incidents,
        parts.bus,
        parts.approvals,
        get_runner(request),
        parts.registry,
        parts.talk.log,
        clock=parts.quiet.clock,
        language=parts.settings_store.language,
    )


async def require_session(request: Request, response: Response) -> str | None:
    """401 unless the request carries a live session cookie; returns the raw token. With the
    login off there is no token, and only a change sent from another site is refused (#611)."""
    if not get_settings(request).login_enabled:
        # No SameSite cookie guards a change now, and a browser sends a proxy's basic auth along
        # with another site's form post, so a change must come from a page of this origin.
        if request.method not in SAFE_METHODS and request.headers.get(
            "sec-fetch-site", "same-origin"
        ) not in ("same-origin", "none"):
            raise Refusal(403, "cross_site")
        return None
    token = request.cookies.get(COOKIE_NAME)
    check = await get_parts(request).sessions.check(token) if token else None
    if token is None or check is None:
        raise Refusal(401, "not_authenticated")
    if check.refreshed:  # the expiry slid, so the browser's copy slides with it
        set_session_cookie(response, token, get_settings(request))
    return token
