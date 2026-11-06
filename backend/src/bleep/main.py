import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from bleep.api import (
    auth,
    dev,
    host_settings,
    hosts,
    incidents,
    memory,
    plugins,
    quiet,
    secrets,
    stream,
    telegram,
)
from bleep.api import settings as settings_api
from bleep.api.body_limit import BodyLimitMiddleware
from bleep.api.deps import require_session
from bleep.config import Settings, check_settings
from bleep.graph.build import build_graph, open_checkpointer
from bleep.hosts.dashboard import HostChange
from bleep.incidents.expectations import Expectations
from bleep.incidents.reminders import ReminderLoop
from bleep.incidents.repository import utcnow
from bleep.incidents.runner import IncidentRunner
from bleep.incidents.watchdog import Watchdog
from bleep.watcher.detector import Detector
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
            stack.push_async_callback(parts.notify.notifier.drain)  # clears in flight finish first
            await parts.db.migrate()
            if settings.dev_panel:  # with the panel off, every prompt is read from its file
                await parts.prompt_overrides.load()
                stack.callback(parts.prompt_overrides.close)
            for config in await parts.dashboard_hosts.load():
                parts.registry.add(config)
                await parts.settings_store.host_changed(HostChange(config.id, "added", config))
            await parts.settings_store.load()  # before the registry starts, so overrides hold
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
                settings=parts.settings_store,
            )
            stack.push_async_callback(runner.close)
            manager = WatcherManager(
                parts.registry,
                Detector(parts.settings_store.thresholds, utcnow),
                parts.incidents,
                runner,
                parts.bus,
                models=parts.graph_deps.models,
                guard_log=parts.graph_deps.guard_log,
                ledger=parts.graph_deps.ledger,
                budgets=parts.settings_store.budgets,
                store=parts.url_checks,
                watch_interval_s=settings.watch_interval_s,
                quiet=parts.quiet,
                health=parts.service_health,
                wake_grace_ticks=settings.wake_grace_ticks,
            )
            runner.host_answered = manager.answered
            reminders = ReminderLoop(
                parts.approvals,
                parts.incidents,
                parts.graph_deps.notifier,
                parts.graph_deps.settings_store,
            )
            watchdog = Watchdog(
                parts.incidents,
                parts.bus,
                parts.graph_deps.notifier,
                parts.graph_deps.settings_store,
                skip=runner.retrying,
            )
            app.state.runner, app.state.reminders = runner, reminders
            app.state.expectations = Expectations(
                parts.incidents,
                runner,
                parts.approvals,
                parts.settings_store,
                parts.registry,
                parts.bus,
            )
            # The manager first: a removed host stops being watched before its settings go.
            parts.dashboard_hosts.subscribe(manager.host_changed)
            parts.dashboard_hosts.subscribe(parts.settings_store.host_changed)
            parts.quiet.subscribe(manager.quiet_changed)
            stack.push_async_callback(manager.stop)
            await manager.start()
            quiet_task = asyncio.create_task(parts.quiet.run(), name="quiet")
            stack.push_async_callback(_cancel, quiet_task)
            app.state.reminder_task = asyncio.create_task(reminders.run(), name="reminders")
            stack.push_async_callback(_cancel, app.state.reminder_task)
            app.state.watchdog_task = asyncio.create_task(watchdog.run(), name="watchdog")
            stack.push_async_callback(_cancel, app.state.watchdog_task)
            knowledge = asyncio.create_task(parts.knowledge_refresher.run(), name="knowledge")
            stack.push_async_callback(_cancel, knowledge)
            stack.push_async_callback(parts.notify.stop)
            parts.notify.use_expectations(app.state.expectations)
            parts.notify.start(runner)  # polls Telegram only when a token is configured
            yield

    app = FastAPI(title="Bleep", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
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
    protected.include_router(settings_api.router)
    protected.include_router(host_settings.router)
    protected.include_router(quiet.router)
    protected.include_router(plugins.router)
    protected.include_router(secrets.router)
    protected.include_router(telegram.router)
    if settings.dev_panel:  # left out, every /api/dev path is a 404, signed in or not
        protected.include_router(dev.router)
    api.include_router(protected)
    app.include_router(api)
    return app
