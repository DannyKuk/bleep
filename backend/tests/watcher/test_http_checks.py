import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.store.db import Database
from bleep.watcher.http_checks import MAX_BODY_BYTES, UrlCheckStore, UrlWatcher, check_url
from fakes import FakeEngine, socket_host
from graph_env import box

URL = "https://shop.example/health"
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def client(handler) -> httpx.AsyncClient:
    async def streamed(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        if asyncio.iscoroutine(response):
            response = await response
        if not response.is_stream_consumed:
            return response
        # a body given as bytes is already read; a real transport hands over a raw stream
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=httpx.ByteStream(response.content),
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(streamed))


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="all good")


async def test_a_matching_reply_passes():
    check = await check_url(client(ok_handler), UrlCheckConfig(url=URL))
    assert (check.ok, check.status, check.error, check.body_matched) == (True, 200, None, None)


async def test_a_wrong_status_fails_and_says_which():
    handler = lambda request: httpx.Response(503)  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL))
    assert not check.ok
    assert check.status == 503
    assert check.error == "expected status 200, got 503"


async def test_a_status_other_than_200_passes_when_expected():
    handler = lambda request: httpx.Response(401)  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL, expect_status=401))
    assert check.ok


async def test_a_connection_error_fails_without_raising():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    check = await check_url(client(refuse), UrlCheckConfig(url=URL))
    assert not check.ok
    assert check.status is None
    assert "ConnectError" in check.error


async def test_a_timeout_error_fails():
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    check = await check_url(client(time_out), UrlCheckConfig(url=URL))
    assert not check.ok
    assert check.error == "timed out after 10 s"


async def test_a_reply_that_never_completes_is_cut_off_at_the_timeout():
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    check = await check_url(client(hang), UrlCheckConfig(url=URL), timeout_s=0.05)
    assert not check.ok
    assert check.error == "no complete reply within 0.05 s"


async def test_a_reply_slower_than_the_limit_fails_even_with_the_right_status():
    ticks = iter([0.0, 5.2])
    check = await check_url(client(ok_handler), UrlCheckConfig(url=URL), clock=lambda: next(ticks))
    assert not check.ok
    assert check.status == 200
    assert check.latency_ms == 5200
    assert check.error == "slow: 5200 ms (limit 5000 ms)"


async def test_a_reply_at_the_limit_passes():
    ticks = iter([0.0, 5.0])
    check = await check_url(client(ok_handler), UrlCheckConfig(url=URL), clock=lambda: next(ticks))
    assert check.ok


async def test_missing_body_text_fails_and_present_text_passes():
    cfg = UrlCheckConfig(url=URL, body_contains="all good")
    assert (await check_url(client(ok_handler), cfg)).body_matched is True
    cfg = UrlCheckConfig(url=URL, body_contains="welcome")
    check = await check_url(client(ok_handler), cfg)
    assert not check.ok
    assert check.body_matched is False
    assert check.error == "the reply does not contain the expected text"


async def test_redirects_are_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(302, headers={"location": "/ready"})
        return httpx.Response(200, text="ready")

    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="ready"))
    assert check.ok


async def test_at_most_64_kb_of_the_body_is_read():
    sent = 0

    async def body():
        nonlocal sent
        for _ in range(100):  # 100 x 8 KB on offer; the check must stop early
            sent += 8192
            yield b"x" * 8192

    handler = lambda request: httpx.Response(200, content=body())  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="needle"))
    assert not check.ok  # the needle is nowhere in what was read
    assert MAX_BODY_BYTES <= sent <= MAX_BODY_BYTES + 8192


async def test_text_after_the_first_64_kb_is_not_seen():
    text = "x" * MAX_BODY_BYTES + "needle"
    handler = lambda request: httpx.Response(200, text=text)  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="needle"))
    assert check.body_matched is False


async def test_the_body_is_not_read_when_no_text_is_asked_for():
    async def body():
        raise AssertionError("the body must not be read")
        yield b""

    handler = lambda request: httpx.Response(200, content=body())  # noqa: E731
    assert (await check_url(client(handler), UrlCheckConfig(url=URL))).ok


async def test_a_binary_body_does_not_break_the_match():
    handler = lambda request: httpx.Response(200, content=b"\xff\xfe ok \x00")  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="ok"))
    assert check.ok


async def test_the_watcher_checks_every_configured_url_and_feeds_each_result(tmp_path):
    urls = [UrlCheckConfig(url="https://a.example/"), UrlCheckConfig(url="https://b.example/")]
    host = socket_host(id="h", services={"compose:shop": ServiceOverrides(urls=urls)})
    engine = FakeEngine("h", [box("shop-web-1", "shop")])

    async def connect(_host):
        return None, engine

    registry = Registry([host], connect)
    await registry.start()
    db = Database(tmp_path / "bleep.sqlite")
    await db.migrate()
    store = UrlCheckStore(db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.host == "a.example" else 500)

    signals = []

    async def sink(signal):
        signals.append(signal)

    watcher = UrlWatcher("h", registry, client(handler), sink, lambda: T0, store)
    await watcher.tick()
    by_url = {s.detail["url"]: s for s in signals}
    assert by_url["https://a.example/"].detail["ok"] is True
    assert by_url["https://b.example/"].detail["ok"] is False
    assert {s.kind for s in signals} == {"url_failed"}  # the kind carries every result
    assert {s.subject.service_id for s in signals} == {"h/compose:shop"}
    latest = await store.latest("h")
    assert [(c.url, c.ok, c.status) for _, c, _ in latest] == [
        ("https://a.example/", True, 200),
        ("https://b.example/", False, 500),
    ]
    await watcher.tick()  # a second tick replaces the row instead of adding one
    assert len(await store.latest("h")) == 2


async def test_a_failing_sink_does_not_drop_the_other_urls(tmp_path):
    urls = [UrlCheckConfig(url="https://a.example/"), UrlCheckConfig(url="https://b.example/")]
    host = socket_host(id="h", services={"compose:shop": ServiceOverrides(urls=urls)})
    engine = FakeEngine("h", [box("shop-web-1", "shop")])

    async def connect(_host):
        return None, engine

    registry = Registry([host], connect)
    await registry.start()
    seen = []

    async def sink(signal):
        seen.append(signal.detail["url"])
        raise RuntimeError("boom")

    watcher = UrlWatcher("h", registry, client(ok_handler), sink, lambda: T0)
    checks = await watcher.tick()
    assert len(checks) == 2
    assert sorted(seen) == ["https://a.example/", "https://b.example/"]


async def test_a_malformed_url_is_a_failed_check_not_an_exception():
    check = await check_url(client(ok_handler), UrlCheckConfig(url="http://host:abc/"))
    assert not check.ok and check.status is None
    assert check.error and "InvalidURL" in check.error and "abc" not in check.error


async def test_an_unexpected_error_from_the_client_is_a_failed_check():
    def explode(request: httpx.Request) -> httpx.Response:
        raise ValueError("boom")

    check = await check_url(client(explode), UrlCheckConfig(url=URL))
    assert not check.ok and check.error == "request failed (ValueError)"


async def test_a_crashing_check_does_not_stop_the_watcher_from_reporting_the_url(monkeypatch):
    from bleep.watcher import http_checks

    async def crash(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(http_checks, "check_url", crash)
    engine = FakeEngine("h", [box("shop-1", "shop")])

    async def connect(_host):
        return None, engine

    registry = Registry(
        [socket_host(id="h", services={"shop": ServiceOverrides(urls=[UrlCheckConfig(url=URL)])})],
        connect,
    )
    await registry.start()
    sent = []

    async def sink(signal):
        sent.append(signal)

    watcher = UrlWatcher("h", registry, client(ok_handler), sink, lambda: T0)
    checks = await watcher.tick()

    assert [c.ok for c in checks] == [False]
    assert sent and sent[0].detail["ok"] is False


async def test_a_gzip_body_that_would_inflate_hugely_is_refused_and_never_inflated():
    import gzip
    import tracemalloc

    bomb = gzip.compress(b"\0" * 200_000_000, 1)
    handler = lambda request: httpx.Response(  # noqa: E731
        200, stream=httpx.ByteStream(bomb), headers={"content-encoding": "gzip"}
    )
    tracemalloc.start()
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="x"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert not check.ok
    assert check.error == "the reply is compressed although plain text was asked for"
    assert peak < 5_000_000


async def test_a_zstd_body_is_refused_unread():
    handler = lambda request: httpx.Response(  # noqa: E731
        200,
        stream=httpx.ByteStream(b"\x28\xb5\x2f\xfd" + b"x" * 100),
        headers={"content-encoding": "zstd"},
    )
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="x"))
    assert not check.ok
    assert "compressed" in check.error


async def test_the_check_asks_for_an_uncompressed_reply():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["accept-encoding"])
        return httpx.Response(200, text="ok")

    await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="ok"))
    assert seen == ["identity"]


async def test_a_redirect_with_a_gzip_bomb_body_is_never_inflated():
    import gzip
    import tracemalloc

    bomb = gzip.compress(b"\0" * 200_000_000, 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                302,
                stream=httpx.ByteStream(bomb),
                headers={"location": "/ok", "content-encoding": "gzip"},
            )
        return httpx.Response(200, text="ready")

    tracemalloc.start()
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="ready"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert check.ok
    assert peak < 5_000_000


async def test_a_redirect_loop_fails_the_check():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    check = await check_url(client(handler), UrlCheckConfig(url=URL))
    assert not check.ok
    assert check.error == "request failed (TooManyRedirects)"


async def test_an_identity_body_far_over_the_cap_is_read_only_up_to_the_cap():
    sent = 0

    async def body():
        nonlocal sent
        for _ in range(100_000):  # 800 MB on offer
            sent += 8192
            yield b"x" * 8192

    handler = lambda request: httpx.Response(200, content=body())  # noqa: E731
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="x"))
    assert check.ok
    assert sent <= MAX_BODY_BYTES + 8192


async def test_a_zstd_body_is_refused_without_memory_growth():
    import tracemalloc

    payload = b"\x28\xb5\x2f\xfd" + b"x" * 1_000_000
    handler = lambda request: httpx.Response(  # noqa: E731
        200, stream=httpx.ByteStream(payload), headers={"content-encoding": "zstd"}
    )
    tracemalloc.start()
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="x"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert not check.ok
    assert peak < 3_000_000


@pytest.mark.parametrize(
    "headers",
    [
        [("content-encoding", "identity, gzip")],
        [("content-encoding", "GZIP")],
        [("content-encoding", "  gzip  ")],
        [("content-encoding", "br")],
        [("content-encoding", "gzip"), ("content-encoding", "gzip")],
    ],
)
async def test_any_compression_header_spelling_is_refused(headers):
    handler = lambda request: httpx.Response(  # noqa: E731
        200, stream=httpx.ByteStream(b"junk"), headers=headers
    )
    check = await check_url(client(handler), UrlCheckConfig(url=URL, body_contains="x"))
    assert not check.ok
    assert check.error == "the reply is compressed although plain text was asked for"
