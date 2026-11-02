from fastapi import HTTPException, Request, Response

from bleep.api.cookie import COOKIE_NAME, set_session_cookie
from bleep.config import Settings
from bleep.wiring import AppParts


def get_parts(request: Request) -> AppParts:
    return request.app.state.parts


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def require_session(request: Request, response: Response) -> str:
    """401 unless the request carries a live session cookie; returns the raw token."""
    token = request.cookies.get(COOKIE_NAME)
    check = await get_parts(request).sessions.check(token) if token else None
    if token is None or check is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if check.refreshed:  # the expiry slid, so the browser's copy slides with it
        set_session_cookie(response, token, get_settings(request))
    return token
