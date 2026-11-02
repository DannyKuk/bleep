"""The SSE stream, driven over raw ASGI: a test client would wait for an endless body to end."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from bleep.api import stream
from bleep.api.cookie import COOKIE_NAME
from bleep.incidents.bus import EventBus
from bleep.main import create_app
from bleep.schemas.incidents import Signal, Subject

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


class Stream:
    """One streaming request against the app: its status, headers and body as it arrives."""

    def __init__(self, app, path: str, query: str = "", headers: dict[str, str] | None = None):
        self.app = app
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "state": {},
        }
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.text = ""
        self._arrived = asyncio.Condition()
        self._requested = False
        self._disconnected = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> Stream:
        self.task = asyncio.create_task(self.app(self.scope, self._receive, self._send))
        return self

    async def __aexit__(self, *exc) -> None:
        self._disconnected.set()
        await asyncio.wait_for(self.task, 5)

    async def _receive(self) -> dict:
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnected.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        async with self._arrived:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode(): v.decode() for k, v in message["headers"]}
            elif message["type"] == "http.response.body":
                self.text += message.get("body", b"").decode()
            self._arrived.notify_all()

    async def until(self, check, timeout: float = 5) -> None:
        async def wait() -> None:
            async with self._arrived:
                await self._arrived.wait_for(lambda: check(self))

        await asyncio.wait_for(wait(), timeout)

    def messages(self) -> list[dict[str, str]]:
        """Each complete SSE message as its fields; comments under the key ":"."""
        found = []
        for block in self.text.replace("\r\n", "\n").split("\n\n")[:-1]:
            fields: dict[str, str] = {}
            for line in block.split("\n"):
                if line.startswith(":"):
                    fields[":"] = line[1:].strip()
                    continue
                key, _, value = line.partition(": ")
                fields[key] = value
            found.append(fields)
        return found

    def events(self) -> list[tuple[str | None, dict]]:
        """(id, decoded data) of every data message, in order."""
        return [(m.get("id"), json.loads(m["data"])) for m in self.messages() if "data" in m]


def seqs(s: Stream) -> list[int]:
    return [data["seq"] for _, data in s.events()]


@pytest.fixture
async def app(settings, parts):
    await parts.db.migrate()
    return create_app(settings, parts)


@pytest.fixture
async def cookie(parts) -> dict[str, str]:
    return {"cookie": f"{COOKIE_NAME}={await parts.sessions.create()}"}


async def open_incident(parts, service: str = "a/shop") -> str:
    subject = Subject(kind="service", host_id="a", service_id=service)
    signal = Signal(subject=subject, kind="container_died", at=T0)
    incident, _ = await parts.incidents.open_or_attach(subject, [signal])
    return incident.id


async def publish(parts, incident_id: str, count: int) -> None:
    for _ in range(count):
        await parts.bus.publish(incident_id, "notice", {"text": "x"})


async def test_reconnect_with_last_event_id_replays_from_the_next_seq(app, parts, cookie):
    incident_id = await open_incident(parts)
    await publish(parts, incident_id, 8)
    headers = cookie | {"last-event-id": "5"}
    async with Stream(app, "/api/stream", f"incident_id={incident_id}", headers) as s:
        await s.until(lambda s: len(s.events()) == 3)
        assert s.status == 200
        assert s.headers["content-type"].startswith("text/event-stream")
        assert [(i, d["seq"]) for i, d in s.events()] == [("6", 6), ("7", 7), ("8", 8)]
        await publish(parts, incident_id, 1)  # then live
        await s.until(lambda s: seqs(s)[-1:] == [9])
        assert s.events()[-1][0] == "9"
        assert s.events()[-1][1]["kind"] == "notice"
        assert s.events()[-1][1]["incident_id"] == incident_id


async def test_without_a_cursor_the_whole_history_comes_first(app, parts, cookie):
    incident_id = await open_incident(parts)
    await publish(parts, incident_id, 3)
    async with Stream(app, "/api/stream", f"incident_id={incident_id}", cookie) as s:
        await s.until(lambda s: len(s.events()) == 3)
        assert seqs(s) == [1, 2, 3]


async def test_after_in_the_query_is_the_first_cursor_and_the_header_wins(app, parts, cookie):
    incident_id = await open_incident(parts)
    await publish(parts, incident_id, 4)
    query = f"incident_id={incident_id}&after=2"
    async with Stream(app, "/api/stream", query, cookie) as s:
        await s.until(lambda s: len(s.events()) == 2)
        assert seqs(s) == [3, 4]
    async with Stream(app, "/api/stream", query, cookie | {"last-event-id": "3"}) as s:
        await s.until(lambda s: len(s.events()) == 1)
        assert seqs(s) == [4]


async def test_the_stream_of_all_incidents_is_live_only_and_carries_no_id(app, parts, cookie):
    one, two = await open_incident(parts, "a/one"), await open_incident(parts, "a/two")
    await publish(parts, one, 2)  # before the stream: not replayed
    async with Stream(app, "/api/stream", "", cookie) as s:
        await s.until(lambda s: s.status is not None)
        await publish(parts, two, 1)
        await publish(parts, one, 1)
        await s.until(lambda s: len(s.events()) == 2)
        assert [(i, d["incident_id"], d["seq"]) for i, d in s.events()] == [
            (None, two, 1),
            (None, one, 3),
        ]


async def test_it_pings_every_15_seconds(app, parts, cookie, monkeypatch):
    assert stream.PING_S == 15
    monkeypatch.setattr(stream, "PING_S", 0.05)
    incident_id = await open_incident(parts)
    async with Stream(app, "/api/stream", f"incident_id={incident_id}", cookie) as s:
        await s.until(lambda s: any(":" in m for m in s.messages()))
        assert any(m.get(":", "").startswith("ping") for m in s.messages())


async def test_closing_the_stream_unsubscribes_from_the_bus(app, parts, cookie):
    incident_id = await open_incident(parts)
    async with Stream(app, "/api/stream", f"incident_id={incident_id}", cookie) as s:
        await s.until(lambda s: s.status is not None)
        await publish(parts, incident_id, 1)
        await s.until(lambda s: len(s.events()) == 1)
        assert parts.bus.subscriber_count == 1
    assert parts.bus.subscriber_count == 0


async def test_the_stream_needs_a_session(app, parts):
    incident_id = await open_incident(parts)
    async with Stream(app, "/api/stream", f"incident_id={incident_id}") as s:
        await s.until(lambda s: s.status is not None)
        assert s.status == 401
    assert parts.bus.subscriber_count == 0


async def test_the_stream_of_an_unknown_incident_is_404(app, cookie):
    async with Stream(app, "/api/stream", "incident_id=inc_nope", cookie) as s:
        await s.until(lambda s: s.status is not None)
        assert s.status == 404


async def test_a_subscriber_that_fell_behind_gets_the_gap_from_history(parts):
    """The bus drops a slow subscriber's oldest events; the stream fills them in."""
    await parts.db.migrate()
    bus = EventBus(parts.db, queue_size=1)
    incident_id = await open_incident(parts)
    await bus.publish(incident_id, "notice", {})
    events = stream.follow(bus, incident_id, after=0)
    assert (await anext(events)).seq == 1  # replayed; now subscribed and reading live
    for _ in range(4):
        await bus.publish(incident_id, "notice", {})  # the queue keeps only seq 5
    got = [(await anext(events)).seq for _ in range(4)]
    assert got == [2, 3, 4, 5]
    await events.aclose()
    assert bus.subscriber_count == 0
