import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, FastAPI

from bleep.api import auth, hosts
from bleep.api.deps import require_session
from bleep.config import Settings, check_settings
from bleep.schemas.settings import Thresholds
from bleep.watcher.detector import Detector
from bleep.watcher.feed import Feed
from bleep.watcher.poller import HostWatcher
from bleep.wiring import AppParts, build_parts

log = logging.getLogger(__name__)


def start_watchers(parts: AppParts, interval_s: float) -> list[asyncio.Task[None]]:
    """One pull-model watcher task per host. Each tick also refreshes the host's snapshot."""
    clock = lambda: datetime.now(UTC)  # noqa: E731
    feed = Feed(Detector(Thresholds(), clock), clock)
    return [
        asyncio.create_task(
            HostWatcher(
                state.config.id,
                parts.registry,
                feed.sink,
                clock,
                interval_s,
                on_snapshot=feed.snapshot,
            ).run(),
            name=f"watcher-{state.config.id}",
        )
        for state in parts.registry.hosts()
    ]


def create_app(settings: Settings | None = None, parts: AppParts | None = None) -> FastAPI:
    """Build the app. Tests pass prebuilt `parts` (a fake-engine registry, a temp database)."""
    settings = settings or Settings()
    check_settings(settings)
    parts = parts or build_parts(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await parts.db.migrate()
        await parts.registry.start()
        watchers = start_watchers(parts, settings.watch_interval_s)
        try:
            yield
        finally:
            for watcher in watchers:
                watcher.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
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
