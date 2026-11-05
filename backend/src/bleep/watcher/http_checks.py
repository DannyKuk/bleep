"""Outside-in URL checks: does the public URL answer the way the service's owner expects?

`check_url` is one check and is also what Verify uses after a fix. `UrlWatcher` runs
every configured URL of one host on a timer and feeds each result to the detector, good or bad,
so its failure streaks can reset. `UrlCheckStore` keeps the latest result per URL and the last
`HISTORY_PER_URL` for the service page (docs/design/watcher.md).
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
from bleep.schemas.api import UrlCheckPoint
from bleep.schemas.incidents import Signal, Subject
from bleep.store.db import Database
from bleep.watcher.wake import SleepWatch

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
MAX_REDIRECTS = 20  # httpx's own default
TIMEOUT_S = 10
SLOW_MS = 5000
INTERVAL_S = 30
HISTORY_PER_URL = 30  # 15 minutes of checks at the default interval

Sink = Callable[[Signal], Awaitable[None]]
RoundHook = Callable[[str], Awaitable[None]]


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
    body (first 64 KB only) without `body_contains`. The error text never quotes the body,
    a header or any other text the server sent.
    """
    started = clock()
    status: int | None = None
    matched: bool | None = None
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_s):  # the whole reply, so a slow drip cannot hang
            response = await _get_following_redirects(client, cfg.url, timeout_s)
            try:
                status = response.status_code
                if cfg.body_contains is not None and status == cfg.expect_status:
                    encoding = response.headers.get("content-encoding", "identity")
                    if encoding.strip().lower() not in ("", "identity"):
                        error = "the reply is compressed although plain text was asked for"
                    else:
                        matched = cfg.body_contains in await _read_capped(response)
            finally:
                await response.aclose()
    except TimeoutError:
        error = f"no complete reply within {timeout_s:g} s"
    except httpx.TimeoutException:
        error = f"timed out after {timeout_s:g} s"
    except Exception as problem:  # httpx.HTTPError, InvalidURL, a bad header: this URL failed
        error = f"request failed ({type(problem).__name__})"  # never the text: a server wrote it
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


async def _get_following_redirects(
    client: httpx.AsyncClient, url: str, timeout_s: float
) -> httpx.Response:
    """The final reply, streamed. Redirects are followed here, not by httpx, because httpx reads
    (and decompresses) the body of every 3xx reply it follows; each one is closed unread."""
    request: httpx.Request | None = client.build_request(
        "GET", url, headers={"Accept-Encoding": "identity"}, timeout=timeout_s
    )
    for _ in range(MAX_REDIRECTS + 1):
        assert request is not None
        response = await client.send(request, stream=True, follow_redirects=False)
        request = response.next_request
        if request is None:
            return response
        await response.aclose()
    raise httpx.TooManyRedirects("too many redirects")


async def _read_capped(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():  # aiter_bytes would inflate first
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES].decode("utf-8", errors="replace")


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
