import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from bleep.api import auth, hosts
from bleep.api.deps import require_session
from bleep.config import Settings, check_settings
from bleep.wiring import AppParts, build_parts

log = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 15


async def _refresh_loop(parts: AppParts) -> None:
    """Refresh every host's snapshot every `REFRESH_INTERVAL_S` seconds, until cancelled.

    A stopgap so the read API does not serve the snapshot from start-up forever. Task 13's
    watcher replaces it. A failed pass is logged and the loop goes on.
    """
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_S)
        try:
            await parts.registry.refresh_all()
        except Exception:
            log.exception("host refresh failed")


def create_app(settings: Settings | None = None, parts: AppParts | None = None) -> FastAPI:
    """Build the app. Tests pass prebuilt `parts` (a fake-engine registry, a temp database)."""
    settings = settings or Settings()
    check_settings(settings)
    parts = parts or build_parts(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await parts.db.migrate()
        await parts.registry.start()
        refresher = asyncio.create_task(_refresh_loop(parts))
        try:
            yield
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)
            await parts.registry.close()

    app = FastAPI(title="Bleep", lifespan=lifespan)
    app.state.settings = settings
    app.state.parts = parts

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    api = APIRouter(prefix="/api")
    api.include_router(auth.login_router)  # the only route open without a session
    protected = APIRouter(dependencies=[Depends(require_session)])
    protected.include_router(auth.session_router)
    protected.include_router(hosts.router)
    api.include_router(protected)
    app.include_router(api)
    return app
