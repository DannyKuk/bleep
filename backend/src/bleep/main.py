from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from bleep.api import auth, hosts
from bleep.api.deps import require_session
from bleep.config import Settings, check_settings
from bleep.wiring import AppParts, build_parts


def create_app(settings: Settings | None = None, parts: AppParts | None = None) -> FastAPI:
    """Build the app. Tests pass prebuilt `parts` (a fake-engine registry, a temp database)."""
    settings = settings or Settings()
    check_settings(settings)
    parts = parts or build_parts(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await parts.db.migrate()
        await parts.registry.start()
        try:
            yield
        finally:
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
