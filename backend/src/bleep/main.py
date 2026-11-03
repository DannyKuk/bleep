import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from bleep.api import auth, hosts, incidents, memory, stream
from bleep.api.deps import require_session
from bleep.config import Settings, check_settings
from bleep.graph.build import build_graph, open_checkpointer
from bleep.incidents.reminders import ReminderLoop
from bleep.incidents.repository import utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.schemas.settings import Thresholds
from bleep.watcher.detector import Detector
from bleep.watcher.http_checks import UrlCheckStore
from bleep.watcher.manager import WatcherManager
from bleep.wiring import AppParts, build_parts

log = logging.getLogger(__name__)


async def _reembed(parts: AppParts) -> None:
    """Embed the memory again after an embedder change. A failure (the model cannot be loaded)
    leaves the old records out of recall until the next boot, but never stops Bleep."""
    try:
        count = await parts.memory.reembed()
    except Exception as error:
        log.warning("memory: re-embedding failed (%s); old records stay out of recall", error)
        return
    if count:
        log.info("memory: re-embedded %d incident(s) with %s", count, parts.memory.embedder.name)


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def create_app(settings: Settings | None = None, parts: AppParts | None = None) -> FastAPI:
    """Build the app. Tests pass prebuilt `parts` (a fake-engine registry, a temp database)."""
    settings = settings or Settings()
    check_settings(settings)
    parts = parts or build_parts(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            # Pushed in start order, so a failure at any point closes exactly what was opened,
            # and a normal stop runs them in reverse: stop what sends, then close what it sends on.
            stack.push_async_callback(parts.notify.close)  # last: runs and reminders still send
            await parts.db.migrate()
            await _reembed(parts)
            await parts.registry.start()
            stack.push_async_callback(parts.registry.close)
            saver = await stack.enter_async_context(
                open_checkpointer(settings.data_dir / "checkpoints.sqlite")
            )
            stack.push_async_callback(parts.guard_log.flush)  # the runs and the watcher record
            stack.push_async_callback(parts.tracer.close)  # after the runs: sends their last spans
            runner = IncidentRunner(
                build_graph(parts.graph_deps, saver),
                parts.incidents,
                parts.bus,
                approvals=parts.approvals,
                notifier=parts.graph_deps.notifier,
                tracer=parts.tracer,
            )
            stack.push_async_callback(runner.close)
            manager = WatcherManager(
                parts.registry,
                Detector(Thresholds(), utcnow),
                parts.incidents,
                runner,
                parts.bus,
                models=parts.graph_deps.models,
                guard_log=parts.graph_deps.guard_log,
                store=UrlCheckStore(parts.db),
                watch_interval_s=settings.watch_interval_s,
            )
            reminders = ReminderLoop(
                parts.approvals,
                parts.incidents,
                parts.graph_deps.notifier,
                parts.graph_deps.settings_store,
            )
            app.state.runner, app.state.reminders = runner, reminders
            stack.push_async_callback(manager.stop)
            await manager.start()
            app.state.reminder_task = asyncio.create_task(reminders.run(), name="reminders")
            stack.push_async_callback(_cancel, app.state.reminder_task)
            stack.push_async_callback(parts.notify.stop)
            parts.notify.start(runner)  # polls Telegram only when a token is configured
            yield

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
    # Before hosts: a service id holds a "/", and `GET /services/{id:path}` would take ".../facts".
    protected.include_router(memory.router)
    protected.include_router(hosts.router)
    protected.include_router(incidents.router)
    protected.include_router(stream.router)
    api.include_router(protected)
    app.include_router(api)
    return app
