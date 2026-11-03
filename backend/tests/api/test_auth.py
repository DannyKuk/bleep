import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bleep.auth.sessions import LoginLimiter
from bleep.config import Settings, SettingsError
from bleep.main import create_app

PASSWORD = "test-password"
T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def login(client, password=PASSWORD):
    return client.post("/api/auth/login", json={"password": password})


def api_routes(app) -> list[tuple[str, str]]:
    """Every (method, path) under /api except login, from the OpenAPI document."""
    return [
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api") and path != "/api/auth/login"
        for method in item
    ]


def test_every_api_route_but_login_needs_a_session(client):
    routes = api_routes(client.app)
    assert {path for _, path in routes} >= {
        "/api/auth/me",
        "/api/auth/logout",
        "/api/hosts",
        "/api/services",
        "/api/incidents",
        "/api/incidents/{incident_id}",
        "/api/incidents/{incident_id}/events",
        "/api/incidents/{incident_id}/decision",
        "/api/recoveries",
        "/api/stream",
        "/api/settings",
        "/api/plugins",
        "/api/plugins/{name}",
    }
    for method, path in routes:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path)
        assert response.json() == {"detail": "not authenticated"}


def test_health_stays_open(client):
    assert client.get("/health").status_code == 200


def test_login_sets_a_hardened_cookie(client):
    response = login(client)
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "bleep_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert f"max-age={7 * 24 * 3600}" in cookie
    assert "secure" not in cookie.replace("samesite", "")
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_cookie_is_secure_when_configured(settings, parts):
    secure = settings.model_copy(update={"cookie_secure": True})
    with TestClient(create_app(secure, parts)) as client:
        assert "secure" in login(client).headers["set-cookie"].lower().replace("samesite", "")


def test_wrong_password_is_401(client):
    response = login(client, "nope")
    assert response.status_code == 401
    assert response.json() == {"detail": "wrong password"}
    assert "set-cookie" not in response.headers


def test_sixth_failure_is_429_with_retry_after_and_success_after_the_window(settings, parts):
    clock = Clock()
    parts.login_limiter = LoginLimiter(clock=clock)
    with TestClient(create_app(settings, parts)) as client:
        for _ in range(5):
            assert login(client, "nope").status_code == 401
        blocked = login(client, "nope")
        assert blocked.status_code == 429
        assert blocked.json() == {"detail": "too many attempts"}
        assert blocked.headers["retry-after"] == "60"
        assert login(client).status_code == 429  # even the right password waits
        clock.now = T0 + timedelta(seconds=61)
        assert login(client).status_code == 204


def test_successful_logins_do_not_count(client):
    for _ in range(8):
        assert login(client).status_code == 204


def test_token_is_stored_hashed(client, parts):
    token = login(client).cookies["bleep_session"]
    raw = parts.db.path.read_bytes()
    assert token.encode() not in raw
    with closing(sqlite3.connect(parts.db.path)) as conn:
        rows = conn.execute("SELECT * FROM sessions").fetchall()
    assert token not in repr(rows)


def test_expired_session_is_401(settings, parts):
    from bleep.auth.sessions import SessionStore

    clock = Clock()
    parts.sessions = SessionStore(parts.db, clock)
    with TestClient(create_app(settings, parts)) as client:
        login(client)
        assert client.get("/api/auth/me").status_code == 200
        clock.now = T0 + timedelta(days=8)
        assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session_and_clears_the_cookie(client):
    login(client)
    token = client.cookies["bleep_session"]
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (
        'bleep_session=""' in response.headers["set-cookie"]
        or "Max-Age=0" in response.headers["set-cookie"]
    )
    client.cookies.set("bleep_session", token)  # replaying the old token fails
    assert client.get("/api/auth/me").status_code == 401


def test_a_forged_cookie_is_401(client):
    client.cookies.set("bleep_session", "forged")
    assert client.get("/api/auth/me").status_code == 401


def test_short_admin_password_is_refused_at_startup(settings, parts):
    short = settings.model_copy(update={"admin_password": SecretStr("short-pw-11")})
    with pytest.raises(SettingsError, match="at least 12"):
        create_app(short, parts)


def test_cookie_secure_defaults_off():
    assert (
        Settings(admin_password=SecretStr("x" * 12), secret=SecretStr("s")).cookie_secure is False
    )
