"""Oversized write bodies are refused by size before they are read (#167)."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bleep.api.body_limit import MAX_BODY_BYTES, BodyLimitMiddleware
from bleep.api.settings import check_size

BIG = b'{"x": "' + b"a" * (MAX_BODY_BYTES + 1) + b'"}'
WRITES = [
    ("/api/settings", "put"),
    ("/api/plugins/coolify", "put"),
    ("/api/dev/prompts/llm/json_retry", "put"),
]


@pytest.fixture
def dev_client(settings, parts):
    from bleep.main import create_app

    settings.dev_panel = True
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


@pytest.fixture
def no_handler(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the handler read the body")

    monkeypatch.setattr("bleep.api.settings.parse_body", refuse)
    monkeypatch.setattr("bleep.api.plugins.parse_body", refuse)
    monkeypatch.setattr("bleep.api.dev.parse_body", refuse)
    monkeypatch.setattr("bleep.api.dev.check_size", refuse)


@pytest.mark.parametrize(("path", "method"), WRITES)
def test_a_declared_oversized_body_is_a_413_without_the_handler_running(
    dev_client, no_handler, path, method
):
    response = dev_client.request(method, path, content=BIG, headers={"If-Match": '"v"'})
    assert response.status_code == 413


@pytest.mark.parametrize(("path", "method"), WRITES)
def test_a_chunked_oversized_body_is_a_413(dev_client, no_handler, path, method):
    def chunks():
        for _ in range(0, len(BIG), 64 * 1024):
            yield b"a" * 64 * 1024

    response = dev_client.request(method, path, content=chunks(), headers={"If-Match": '"v"'})
    assert response.status_code == 413


async def test_a_chunked_read_stops_at_the_cap():
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": b"a" * 1000, "more_body": True}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        raise AssertionError("the app ran")

    scope = {"type": "http", "method": "PUT", "path": "/api/settings", "headers": []}
    await BodyLimitMiddleware(app, max_bytes=5000)(scope, receive, send)
    assert sent[0]["status"] == 413
    assert calls == 6


def test_a_normal_body_still_works(dev_client):
    version = dev_client.get("/api/settings").json()["version"]
    response = dev_client.put("/api/settings", json={}, headers={"If-Match": f'"{version}"'})
    assert response.status_code == 200


def test_other_routes_are_not_limited(client):
    response = client.post("/api/auth/login", content=BIG, headers={"content-type": "text/plain"})
    assert response.status_code != 413


def test_check_size_refuses_a_body_nested_too_deeply_to_measure():
    body: list = []
    for _ in range(200_000):
        body = [body]
    with pytest.raises(HTTPException) as refusal:
        check_size(body)
    assert refusal.value.status_code == 422


def test_a_deeply_nested_request_is_refused_not_a_500(dev_client):
    version = dev_client.get("/api/settings").json()["version"]
    deep = b"[" * 50_000 + b"]" * 50_000
    response = dev_client.put(
        "/api/settings",
        content=deep,
        headers={"If-Match": f'"{version}"', "content-type": "application/json"},
    )
    assert response.status_code in (413, 422)
    assert "detail" in json.loads(response.content)
