"""The dev panel's routes: off unless switched on, behind the session, prompt overrides checked
like the loader, costs that match the ledger, and the guard log filtered (#42)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from bleep import prompts
from bleep.incidents.repository import IncidentRepo
from bleep.main import create_app
from bleep.schemas.incidents import Signal, Subject

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
GITHUB_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
RECOVERY = "watcher/recovery_line"
DEV_GETS = ["/api/dev", "/api/dev/prompts", "/api/dev/costs", "/api/dev/guard-log"]


def recovery_file() -> str:
    return prompts.file_text(RECOVERY)


@pytest.fixture
def dev_client(settings, parts):
    """A logged-in client with the dev panel on."""
    settings.dev_panel = True
    parts.incidents = IncidentRepo(parts.db, lambda: T0)
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        yield client


def call(client: TestClient, fn, *args):
    """Run one of the app's async parts on the app's own event loop."""
    return client.portal.call(fn, *args)


def open_incident(client: TestClient, parts, service: str) -> str:
    subject = Subject(kind="service", host_id="a", service_id=service)
    signal = Signal(subject=subject, kind="container_died", at=T0, container="web-1")
    incident, _ = call(client, parts.incidents.open_or_attach, subject, [signal])
    return incident.id


# --- off ---


@pytest.mark.parametrize("path", DEV_GETS)
def test_with_the_panel_off_every_dev_route_is_a_404_signed_in_or_not(client, path):
    assert client.get(path).status_code == 404
    assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
    assert client.get(path).status_code == 404


def test_with_the_panel_off_no_override_can_be_written_or_reset(client):
    client.post("/api/auth/login", json={"password": "test-password"})
    path = f"/api/dev/prompts/{RECOVERY}"
    assert client.put(path, json={"text": "{logs}"}).status_code == 404
    assert client.delete(path).status_code == 404


def test_with_the_panel_off_a_stored_override_is_not_used(settings, parts):
    settings.dev_panel = True
    with TestClient(create_app(settings, parts)) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        text = "Overridden: {logs}"
        assert client.put(f"/api/dev/prompts/{RECOVERY}", json={"text": text}).status_code == 200
    settings.dev_panel = False
    with TestClient(create_app(settings, parts)):
        assert prompts.load(RECOVERY, logs="x") == recovery_file().format(logs="x")


@pytest.mark.parametrize("path", DEV_GETS)
def test_with_the_panel_on_the_routes_need_the_session(settings, parts, path):
    settings.dev_panel = True
    with TestClient(create_app(settings, parts)) as client:
        assert client.get(path).status_code == 401


# --- prompts ---


def test_the_prompt_list_has_every_app_prompt_with_its_file_and_placeholders(dev_client):
    body = dev_client.get("/api/dev/prompts").json()
    names = [p["name"] for p in body]
    assert names == prompts.overridable_names()
    assert "graph/propose" in names
    assert not [n for n in names if n.startswith("evals/")]
    recovery = next(p for p in body if p["name"] == RECOVERY)
    assert recovery["placeholders"] == ["logs"]
    assert recovery["file_text"] == recovery_file()
    assert recovery["override"] is None


def test_a_saved_override_is_what_the_loader_returns_and_reset_restores_the_file(dev_client):
    path = f"/api/dev/prompts/{RECOVERY}"
    text = "Why did it come back? {logs}"

    saved = dev_client.put(path, json={"text": text})
    assert saved.status_code == 200
    assert saved.json()["override"] == text
    assert saved.json()["overridden_at"] is not None
    assert prompts.load(RECOVERY, logs="LOGS") == "Why did it come back? LOGS"
    listed = next(p for p in dev_client.get("/api/dev/prompts").json() if p["name"] == RECOVERY)
    assert listed["override"] == text

    reset = dev_client.delete(path)
    assert reset.status_code == 200
    assert reset.json()["override"] is None
    assert reset.json()["file_text"] == recovery_file()
    assert prompts.load(RECOVERY, logs="LOGS") == recovery_file().format(logs="LOGS")


def test_an_override_stays_in_force_after_a_restart(settings, parts, dev_client):
    dev_client.put(f"/api/dev/prompts/{RECOVERY}", json={"text": "Kept: {logs}"})
    prompts.use_overrides({})
    with TestClient(create_app(settings, parts)):
        assert prompts.load(RECOVERY, logs="x") == "Kept: x"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Why did it come back? {logs} {container}", "unknown: {container}"),
        ("Why did it come back?", "missing: {logs}"),
        ("Why did it come back? {logs", "braces"),
        ("Why {logs} did it come back? }", "braces"),
        ("Why {logs!r}", "placeholders"),
    ],
)
def test_an_override_the_loader_would_refuse_is_a_422_and_not_saved(dev_client, text, why):
    response = dev_client.put(f"/api/dev/prompts/{RECOVERY}", json={"text": text})
    assert response.status_code == 422
    assert why in response.json()["detail"]
    assert prompts.load(RECOVERY, logs="x") == recovery_file().format(logs="x")


@pytest.mark.parametrize(
    "body",
    [
        {"text": f"Push with {GITHUB_TOKEN} {{logs}}"},
        {"text": "{logs}", "token": "abc-secret-value"},
    ],
)
def test_an_override_holding_a_secret_is_a_422_that_does_not_echo_it(dev_client, body):
    response = dev_client.put(f"/api/dev/prompts/{RECOVERY}", json=body)
    assert response.status_code == 422
    assert "ghp_" not in response.text and "abc-secret-value" not in response.text
    assert dev_client.get("/api/dev/prompts").json()[0]["override"] is None


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "x" * 20_001}, {}, {"txt": "{logs}"}])
def test_an_invalid_body_is_a_422(dev_client, body):
    assert dev_client.put(f"/api/dev/prompts/{RECOVERY}", json=body).status_code == 422


@pytest.mark.parametrize("name", ["graph/nope", "evals/judge_system", "../config", "README"])
def test_an_unknown_prompt_is_a_404(dev_client, name):
    assert dev_client.put(f"/api/dev/prompts/{name}", json={"text": "hi"}).status_code == 404
    assert dev_client.delete(f"/api/dev/prompts/{name}").status_code == 404


# --- costs ---


def test_costs_match_the_ledger_per_incident_and_per_day(dev_client, parts):
    shop = open_incident(dev_client, parts, "a/shop")
    worker = open_incident(dev_client, parts, "a/worker")
    call(
        dev_client, parts.incidents.set_trace_url, shop, "http://localhost:3001/project/x/traces/1"
    )
    ledger = parts.graph_deps.ledger
    call(dev_client, ledger.add, shop, "openai/gpt-6-luna", 1000, 100, 0.25)
    call(dev_client, ledger.add, worker, "mystery/m", 400, 40, None)
    call(dev_client, ledger.add, shop, "openai/gpt-6-luna", 500, 50, 0.125)

    body = dev_client.get("/api/dev/costs").json()

    by_id = {row["incident_id"]: row for row in body["incidents"]}
    assert [row["incident_id"] for row in body["incidents"]] == [shop, worker]
    assert by_id[shop]["calls"] == 2
    assert (by_id[shop]["tokens_in"], by_id[shop]["tokens_out"]) == (1500, 150)
    assert by_id[shop]["usd"] == pytest.approx(0.375)
    assert by_id[shop]["trace_url"] == "http://localhost:3001/project/x/traces/1"
    assert by_id[shop]["title"] == "container died on a/shop"
    assert by_id[shop]["status"] == "open"
    assert (by_id[worker]["usd"], by_id[worker]["unpriced"]) == (None, 1)
    assert by_id[worker]["trace_url"] is None
    for incident_id, row in by_id.items():
        total = call(dev_client, ledger.incident_total, incident_id)
        assert (row["tokens_in"] + row["tokens_out"], row["usd"]) == (total.tokens, total.usd)
    [today] = body["days"]
    assert (today["calls"], today["tokens_in"], today["unpriced"]) == (3, 1900, 1)
    assert today["usd"] == pytest.approx(call(dev_client, ledger.spent_today))
    assert body["spent_today_usd"] == pytest.approx(0.375)
    assert body["daily_cap_usd"] == 1.0


def test_a_ledger_row_without_an_incident_is_listed_without_one(dev_client, parts):
    call(dev_client, parts.graph_deps.ledger.add, "not-an-incident", "m", 1, 1, 0.0)
    [row] = dev_client.get("/api/dev/costs").json()["incidents"]
    assert (row["incident_id"], row["title"], row["trace_url"]) == ("not-an-incident", None, None)


def test_a_budget_saved_through_the_settings_is_the_cap_the_costs_show(dev_client):
    version = dev_client.get("/api/settings").json()["version"]
    response = dev_client.put(
        "/api/settings",
        json={"budgets": {"daily_cost_usd": 2.5}},
        headers={"If-Match": f'"{version}"'},
    )
    assert response.status_code == 200
    assert dev_client.get("/api/dev/costs").json()["daily_cap_usd"] == 2.5


# --- guard log ---


def test_the_guard_log_is_newest_first_and_filters_by_kind_and_incident(dev_client, parts):
    log = parts.guard_log
    log.record("inc-1", "refused_command", "rm -rf /")
    log.record("inc-2", "redacted", "1 secret(s) redacted from logs output")
    log.record("inc-1", "redacted", "2 secret(s) redacted from inspect output")

    every = dev_client.get("/api/dev/guard-log").json()
    assert [e["detail"] for e in every] == [
        "2 secret(s) redacted from inspect output",
        "1 secret(s) redacted from logs output",
        "rm -rf /",
    ]
    redacted = dev_client.get("/api/dev/guard-log", params={"kind": "redacted"}).json()
    assert [e["incident_id"] for e in redacted] == ["inc-1", "inc-2"]
    one = dev_client.get("/api/dev/guard-log", params={"incident_id": "inc-1"}).json()
    assert [e["kind"] for e in one] == ["redacted", "refused_command"]
    both = dev_client.get(
        "/api/dev/guard-log", params={"incident_id": "inc-2", "kind": "refused_command"}
    ).json()
    assert both == []


def test_an_unknown_guard_kind_is_a_422(dev_client):
    assert dev_client.get("/api/dev/guard-log", params={"kind": "nope"}).status_code == 422
