from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

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


def _origin_host(origin: str) -> str | None:
    parts = urlsplit(origin)
    return parts.netloc.lower() if parts.scheme in ("http", "https") and parts.netloc else None


def _own_hosts(request: Request) -> set[str]:
    """The host:port a page of this dashboard has: the Host the backend sees, the host the
    Next.js rewrite in front forwards as X-Forwarded-Host (it sets its own address as Host), and
    BLEEP_DASHBOARD_URL. Another site's page cannot set these headers on a browser request."""
    named = [
        request.headers.get("host", ""),
        *request.headers.get("x-forwarded-host", "").split(","),
    ]
    dashboard_url = get_settings(request).dashboard_url
    if dashboard_url is not None:
        named.append(urlsplit(str(dashboard_url)).netloc)
    return {host.strip().lower() for host in named if host.strip()}


def refuse_cross_site(request: Request) -> None:
    """403 for a change sent from a page of another site, login on or off (#611, #619): a
    browser sends the session cookie (SameSite=Lax) with a post from a sibling subdomain, and
    a proxy's basic auth with any site's post. `Sec-Fetch-Site` decides; a browser too old to
    send it is judged by its `Origin`, and a request with neither is not a browser's."""
    if request.method in SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site not in ("same-origin", "none"):
            raise Refusal(403, "cross_site")
        return
    origin = request.headers.get("origin")
    if origin is not None and _origin_host(origin) not in _own_hosts(request):
        raise Refusal(403, "cross_site")


async def require_session(request: Request, response: Response) -> str | None:
    """401 unless the request carries a live session cookie; returns the raw token. With the
    login off there is no token and every request passes (#611)."""
    if not get_settings(request).login_enabled:
        return None
    token = request.cookies.get(COOKIE_NAME)
    check = await get_parts(request).sessions.check(token) if token else None
    if token is None or check is None:
        raise Refusal(401, "not_authenticated")
    if check.refreshed:  # the expiry slid, so the browser's copy slides with it
        set_session_cookie(response, token, get_settings(request))
    return token
