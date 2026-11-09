"""The watcher's URL checks: does the public URL answer the way the service's owner expects?

`UrlWatcher` runs `check_url` (`hosts/url_check.py`, which Verify uses after a fix too) on
every configured URL of one host on a timer and feeds each result to the detector, good or bad,
so its failure streaks can reset. `UrlCheckStore` keeps the latest result per URL and the last
`HISTORY_PER_URL` for the service page (docs/design/watcher.md).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.hosts.url_check import UrlCheck, check_url
from bleep.schemas.api import UrlCheckPoint
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database
from bleep.watcher.wake import SleepWatch

log = logging.getLogger(__name__)

INTERVAL_S = 30
HISTORY_PER_URL = 30  # 15 minutes of checks at the default interval

Sink = Callable[[Signal], Awaitable[None]]
RoundHook = Callable[[str], Awaitable[None]]


class UrlCheckStore:
    """The latest result per (host, service, url), and the last `HISTORY_PER_URL` of them without
    their error text, for the dashboard."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, host_id: str, service_id: str, check: UrlCheck, at: datetime) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "INSERT OR REPLACE INTO url_checks (host_id, url, service_id, ok, status,"
                " latency_ms, error, body_matched, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    host_id,
                    check.url,
                    service_id,
                    check.ok,
                    check.status,
                    check.latency_ms,
                    check.error,
                    None if check.body_matched is None else check.body_matched,
                    at.isoformat(),
                ),
            )
            key = (host_id, service_id, check.url)
            await conn.execute(
                "INSERT INTO url_check_history (host_id, service_id, url, ok, status, latency_ms,"
                " checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, check.ok, check.status, check.latency_ms, at.isoformat()),
            )
            await conn.execute(
                "DELETE FROM url_check_history WHERE host_id = ? AND service_id = ? AND url = ?"
                " AND id NOT IN (SELECT id FROM url_check_history WHERE host_id = ?"
                " AND service_id = ? AND url = ? ORDER BY id DESC LIMIT ?)",
                (*key, *key, HISTORY_PER_URL),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def history(self, host_id: str, service_id: str) -> dict[str, list[UrlCheckPoint]]:
        """The service's recent checks by URL, oldest first."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM url_check_history WHERE host_id = ? AND service_id = ? ORDER BY id",
                (host_id, service_id),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        history: dict[str, list[UrlCheckPoint]] = {}
        for row in rows:
            history.setdefault(row["url"], []).append(
                UrlCheckPoint(
                    ok=bool(row["ok"]),
                    status_code=row["status"],
                    latency_ms=row["latency_ms"],
                    checked_at=datetime.fromisoformat(row["checked_at"]),
                )
            )
        return history

    async def latest(self, host_id: str) -> list[tuple[str, UrlCheck, datetime]]:
        """(service id, check, when) for every URL of the host, ordered by URL."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM url_checks WHERE host_id = ? ORDER BY url", (host_id,)
            ) as cursor:
                return [
                    (
                        row["service_id"],
                        UrlCheck(
                            url=row["url"],
                            ok=bool(row["ok"]),
                            status=row["status"],
                            latency_ms=row["latency_ms"],
                            error=row["error"],
                            body_matched=(
                                None if row["body_matched"] is None else bool(row["body_matched"])
                            ),
                        ),
                        datetime.fromisoformat(row["checked_at"]),
                    )
                    for row in await cursor.fetchall()
                ]
        finally:
            await conn.close()


class UrlWatcher:
    """Checks every URL configured for the services of one host, once per `tick()`, then calls
    `on_round` with the host id once the round's results are stored."""

    def __init__(
        self,
        host_id: str,
        registry: Registry,
        client: httpx.AsyncClient,
        sink: Sink,
        clock: Callable[[], datetime],
        store: UrlCheckStore | None = None,
        interval_s: float = INTERVAL_S,
        on_round: RoundHook | None = None,
        sleep_watch: SleepWatch | None = None,
    ) -> None:
        self.host_id = host_id
        self.interval_s = interval_s
        self.on_round = on_round
        self._registry = registry
        self._client = client
        self._sink = sink
        self._clock = clock
        self._store = store
        self._sleep_watch = sleep_watch

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> list[UrlCheck]:
        """Check every URL at once and feed each result to the sink. Never raises."""
        targets = [
            (service.id, cfg)
            for service in self._registry.services(self.host_id)
            for cfg in service.urls
        ]
        checks = list(await asyncio.gather(*(self._one(sid, cfg) for sid, cfg in targets)))
        if self.on_round is not None:
            try:
                await self.on_round(self.host_id)
            except Exception:
                log.exception("url round hook failed for host %s", self.host_id)
        return checks

    async def _one(self, service_id: str, cfg: UrlCheckConfig) -> UrlCheck:
        try:
            check = await check_url(self._client, cfg)
        except Exception as problem:  # check_url does not raise; if it ever does, this URL failed
            log.exception("url check %s for host %s crashed", cfg.url, self.host_id)
            check = UrlCheck(
                url=cfg.url,
                ok=False,
                latency_ms=0,
                error=f"check crashed: {type(problem).__name__}",
            )
        if self._sleep_watch is not None:  # a check across Bleep's own sleep is fed after the wake
            try:
                await self._sleep_watch.check()
            except Exception:
                log.exception("checking for a sleep failed on host %s", self.host_id)
        at = self._clock()
        signal = Signal(
            subject=Subject(kind="service", host_id=self.host_id, service_id=service_id),
            kind="url_failed",  # the kind means "a URL check result"; detail.ok says which
            at=at,
            detail={
                "ok": check.ok,
                "latency_ms": check.latency_ms,
                "url": check.url,
                "status": check.status,
                "error": check.error,
            },
        )
        try:
            await self._sink(signal)
            if self._store is not None:
                await self._store.record(self.host_id, service_id, check, at)
        except Exception:  # one bad consumer must not drop the other URLs of the tick
            log.exception("url check %s for host %s: handling failed", cfg.url, self.host_id)
        return check
