import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from bleep.api.cookie import COOKIE_NAME, set_session_cookie
from bleep.api.deps import get_parts, get_settings, require_session
from bleep.schemas.api import Me
from bleep.wiring import AppParts

login_router = APIRouter(prefix="/auth", tags=["auth"])
session_router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@login_router.post("/login", status_code=204)
async def login(
    body: LoginBody, request: Request, parts: Annotated[AppParts, Depends(get_parts)]
) -> Response:
    # The direct peer, not X-Forwarded-For: a client-supplied header would let anyone reset
    # their own limit. Bleep is LAN-only for now.
    client = request.client.host if request.client else "unknown"
    wait = parts.login_limiter.retry_after(client)
    if wait is not None:
        raise HTTPException(429, "too many attempts", headers={"Retry-After": str(wait)})
    settings = get_settings(request)
    expected = settings.admin_password.get_secret_value().encode()
    if not hmac.compare_digest(body.password.encode(), expected):
        parts.login_limiter.fail(client)
        raise HTTPException(401, "wrong password")
    response = Response(status_code=204)
    set_session_cookie(response, await parts.sessions.create(), settings)
    return response


@session_router.post("/logout", status_code=204)
async def logout(
    token: Annotated[str, Depends(require_session)],
    parts: Annotated[AppParts, Depends(get_parts)],
) -> Response:
    await parts.sessions.revoke(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@session_router.get("/me")
def me() -> Me:
    return Me(authenticated=True)
