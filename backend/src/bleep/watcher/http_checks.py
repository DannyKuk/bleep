"""Outside-in URL checks: does the public URL answer the way the service's owner expects?

`check_url` is one check and is also what Verify uses after a fix. `UrlWatcher` runs
every configured URL of one host on a timer and feeds each result to the detector, good or bad,
so its failure streaks can reset (docs/design/watcher.md).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
from pydantic import BaseModel

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
TIMEOUT_S = 10
SLOW_MS = 5000
INTERVAL_S = 30

Sink = Callable[[Signal], Awaitable[None]]


class UrlCheck(BaseModel):
    url: str
    ok: bool
    status: int | None = None
    latency_ms: int
    error: str | None = None
    body_matched: bool | None = None  # None: no body text was asked for, or no reply came


async def check_url(
    client: httpx.AsyncClient,
    cfg: UrlCheckConfig,
    clock: Callable[[], float] = time.monotonic,
    timeout_s: float = TIMEOUT_S,
    slow_ms: int = SLOW_MS,
) -> UrlCheck:
    """One GET, redirects followed. Never raises: every failure is a `UrlCheck` with `ok` False.

    Fails on a wrong status, a timeout, a connection error, a reply slower than `slow_ms` or a
    body (first 64 KB only) without `body_contains`. The error text never quotes the body.
    """
    started = clock()
    status: int | None = None
    matched: bool | None = None
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_s):  # the whole reply, so a slow drip cannot hang
            async with client.stream(
                "GET", cfg.url, follow_redirects=True, timeout=timeout_s
            ) as response:
                status = response.status_code
                if cfg.body_contains is not None and status == cfg.expect_status:
                    body = await _read_capped(response)
                    matched = cfg.body_contains in body
    except TimeoutError:
        error = f"no complete reply within {timeout_s:g} s"
    except httpx.TimeoutException:
        error = f"timed out after {timeout_s:g} s"
    except Exception as problem:  # httpx.HTTPError, InvalidURL, a bad header: this URL failed
        error = f"{type(problem).__name__}: {problem}".strip(": ")
    latency_ms = round((clock() - started) * 1000)
    if error is None:
        if status != cfg.expect_status:
            error = f"expected status {cfg.expect_status}, got {status}"
        elif matched is False:
            error = "the reply does not contain the expected text"
        elif latency_ms > slow_ms:
            error = f"slow: {latency_ms} ms (limit {slow_ms} ms)"
    return UrlCheck(
        url=cfg.url,
        ok=error is None,
        status=status,
        latency_ms=latency_ms,
        error=error,
        body_matched=matched,
    )


async def _read_capped(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES].decode("utf-8", errors="replace")


class UrlCheckStore:
    """The latest result per (host, url), for the dashboard."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, host_id: str, service_id: str, check: UrlCheck, at: datetime) -> None:
        conn = await self.db.connect()
        try:
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
            await conn.commit()
        finally:
            await conn.close()

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
    """Checks every URL configured for the services of one host, once per `tick()`."""

    def __init__(
        self,
        host_id: str,
        registry: Registry,
        client: httpx.AsyncClient,
        sink: Sink,
        clock: Callable[[], datetime],
        store: UrlCheckStore | None = None,
        interval_s: float = INTERVAL_S,
    ) -> None:
        self.host_id = host_id
        self.interval_s = interval_s
        self._registry = registry
        self._client = client
        self._sink = sink
        self._clock = clock
        self._store = store

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
        return list(await asyncio.gather(*(self._one(sid, cfg) for sid, cfg in targets)))

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
