"""Outside-in URL check: does the public URL answer the way the service's owner expects?

`check_url` is one GET of one URL. The watcher's URL checks run it on a timer
(`watcher/http_checks.py`) and Verify runs it after a fix (docs/design/watcher.md).
"""

import asyncio
import time
from collections.abc import Callable

import httpx
from pydantic import BaseModel

from bleep.hosts.config import UrlCheckConfig

MAX_BODY_BYTES = 64 * 1024
MAX_REDIRECTS = 20  # httpx's own default
TIMEOUT_S = 10
SLOW_MS = 5000


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
