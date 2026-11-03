"""Prompt-injection hook for Bleep's injection tests (backend/evals/injection.py).

app.py mounts it only when DEMO_INJECT=1; the shop's Compose file sets 0. shop-proxy answers
/inject with 404, so only something inside the Compose network (`docker exec shop-web ...`) can
call it.

    POST /inject        {"lines": ["..."], "fail": {"status": 500, "body": "...",
                         "media_type": "text/plain"}}
    POST /inject/clear  ends the failure

Each line is printed to the log as it is, so a test controls the whole line Bleep reads. With
`fail`, every other request (the health check, /api/items) answers that status and body until
the failure is cleared or the container restarts.
"""

import sys

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

MAX_LINES = 50
MAX_TEXT = 8000


class Failure(BaseModel):
    status: int = Field(500, ge=400, le=599)
    body: str = Field("", max_length=MAX_TEXT)
    media_type: str = "text/plain"


class Injection(BaseModel):
    lines: list[str] = Field(default_factory=list, max_length=MAX_LINES)
    fail: Failure | None = None


def install(app: FastAPI) -> None:
    """Add the hook's two routes and the middleware that serves the failure."""
    failing: dict[str, Failure] = {}

    @app.post("/inject")
    def inject(injection: Injection) -> dict:
        for line in injection.lines:
            print(line[:MAX_TEXT], file=sys.stdout, flush=True)
        if injection.fail is not None:
            failing["now"] = injection.fail
        return {"lines": len(injection.lines), "failing": injection.fail is not None}

    @app.post("/inject/clear")
    def clear() -> dict:
        failing.clear()
        return {"failing": False}

    @app.middleware("http")
    async def fail(request: Request, call_next):
        failure = failing.get("now")
        if failure is None or request.url.path.startswith("/inject"):
            return await call_next(request)
        return Response(failure.body, status_code=failure.status, media_type=failure.media_type)
