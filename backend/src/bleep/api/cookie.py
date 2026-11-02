from fastapi import Response

from bleep.auth.sessions import SESSION_TTL
from bleep.config import Settings

COOKIE_NAME = "bleep_session"


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
