"""The API's error details in the picked language (#456): one case per route group, in English
(what the dashboard showed before) and in German."""

import pytest
from fastapi.testclient import TestClient

from bleep.main import create_app

LOGIN = {"password": "test-password"}


@pytest.fixture
def signed_in(settings, parts):
    settings.dev_panel = True
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json=LOGIN).status_code == 204
        yield client


def pick(parts, monkeypatch, language: str) -> None:
    monkeypatch.setattr(parts.settings_store, "language", lambda: language)


def settings_headers(client: TestClient) -> dict[str, str]:
    return {"If-Match": client.get("/api/settings").json()["version"]}


# (id, method, path, request options, status, English detail, German detail)
ROUTES = [
    (
        "memory: a fact of a service that is not there",
        "POST",
        "/api/services/a/none/facts",
        {"json": {"text": "hi"}},
        404,
        "no such service",
        "Diesen Dienst gibt es nicht",
    ),
    (
        "memory: a fact that is not there",
        "DELETE",
        "/api/services/a/none/facts/9",
        {},
        404,
        "no such fact",
        "Diesen Fakt gibt es nicht",
    ),
    (
        "hosts: a service that is not there",
        "GET",
        "/api/services/a/none",
        {},
        404,
        "no such service",
        "Diesen Dienst gibt es nicht",
    ),
    (
        "quiet: a host that is not there",
        "PUT",
        "/api/quiet/hosts/none",
        {"json": {"mode": "leave_alone"}},
        404,
        "no such host",
        "Diesen Host gibt es nicht",
    ),
    (
        "stream: an incident that is not there",
        "GET",
        "/api/stream?incident_id=none",
        {},
        404,
        "no such incident",
        "Diesen Vorfall gibt es nicht",
    ),
    (
        "talk: a message about an incident that is not there",
        "POST",
        "/api/talk/messages",
        {"json": {"text": "hi", "incident_id": "none"}},
        404,
        "no such incident",
        "Diesen Vorfall gibt es nicht",
    ),
    (
        "talk: a look at an incident that is not there",
        "GET",
        "/api/talk/looks/none",
        {},
        404,
        "no such incident",
        "Diesen Vorfall gibt es nicht",
    ),
    (
        "incidents: an incident that is not there",
        "GET",
        "/api/incidents/none",
        {},
        404,
        "no such incident",
        "Diesen Vorfall gibt es nicht",
    ),
    (
        "plugins: a plugin that is not there",
        "PUT",
        "/api/plugins/none",
        {"json": {}},
        404,
        "no such plugin",
        "Dieses Plugin gibt es nicht",
    ),
    (
        "plugins: no version sent",
        "PUT",
        "/api/plugins/coolify",
        {"json": {}},
        428,
        "send the plugin's version you read in If-Match",
        "Sende die Version des Plugins, die du gelesen hast, in If-Match",
    ),
    (
        "settings: no version sent",
        "PUT",
        "/api/settings",
        {"json": {}},
        428,
        "send the settings version you read in If-Match",
        "Sende die Version der Einstellungen, die du gelesen hast, in If-Match",
    ),
    (
        "settings: the budgets while the dev panel is off",
        "PUT",
        "/api/settings",
        {"json": {"budgets": {"replans": 2}}, "dev_panel": False},
        403,
        "the budgets are changed in the dev panel, which is off; "
        "set BLEEP_DEV_PANEL=true to change them",
        "Die Budgets werden im Dev-Panel geändert, und das ist aus; "
        "setze BLEEP_DEV_PANEL=true, um sie zu ändern",
    ),
    (
        "settings: a body that is not JSON",
        "PUT",
        "/api/settings",
        {"content": b"x=1", "headers": {"content-type": "application/x-www-form-urlencoded"}},
        422,
        "send the body as JSON",
        "Sende den Inhalt als JSON",
    ),
    (
        "secrets: a secret that is not there",
        "PUT",
        "/api/secrets/NONE",
        {"json": {"value": "x"}},
        404,
        "no such secret can be set from the dashboard",
        "Dieses Secret lässt sich nicht im Dashboard setzen",
    ),
    (
        "secrets: a secret with no value",
        "PUT",
        "/api/secrets/GITHUB_TOKEN",
        {"json": {"value": "  "}},
        422,
        "GITHUB_TOKEN is empty; clear it instead",
        "GITHUB_TOKEN ist leer; lösche es stattdessen",
    ),
    (
        "hosts on the dashboard: no version sent",
        "DELETE",
        "/api/settings/hosts/none",
        {},
        428,
        "send the host's version you read in If-Match",
        "Sende die Version des Hosts, die du gelesen hast, in If-Match",
    ),
    (
        "hosts on the dashboard: a host that is not there",
        "DELETE",
        "/api/settings/hosts/none",
        {"headers": {"If-Match": "x"}},
        404,
        "no such host was added on the dashboard",
        "Diesen Host hast du nicht im Dashboard hinzugefügt",
    ),
    (
        "dev panel: a prompt that is not there",
        "DELETE",
        "/api/dev/prompts/none",
        {"headers": {"If-Match": "x"}},
        404,
        "no such prompt",
        "Diesen Prompt gibt es nicht",
    ),
    (
        "dev panel: no version sent",
        "DELETE",
        "/api/dev/prompts/graph/diagnose",
        {},
        428,
        "send the prompt's version you read in If-Match",
        "Sende die Version des Prompts, die du gelesen hast, in If-Match",
    ),
    (
        "dev panel: a line break the loader refuses",
        "PUT",
        "/api/dev/prompts/graph/diagnose",
        {"json": {"text": "a\rb"}, "headers": {"If-Match": "x"}},
        422,
        "line breaks must be plain newlines; CR, CRLF, U+2028 and U+2029 are not allowed",
        "Zeilenumbrüche müssen einfache Newlines sein; "
        "CR, CRLF, U+2028 und U+2029 sind nicht erlaubt",
    ),
]


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("case", ROUTES, ids=[case[0] for case in ROUTES])
def test_a_route_error_reads_in_the_picked_language(signed_in, parts, monkeypatch, case, language):
    _, method, path, options, status, english, german = case
    options = dict(options)
    if options.pop("dev_panel", True) is False:
        signed_in.app.state.settings.dev_panel = False
    pick(parts, monkeypatch, language)
    if path == "/api/settings" and status != 428:
        options["headers"] = {**options.get("headers", {}), **settings_headers(signed_in)}

    response = signed_in.request(method, path, **options)

    assert response.status_code == status
    assert response.json()["detail"] == (english if language == "en" else german)


def test_a_wrong_password_and_a_missing_session_are_said_in_the_picked_language(
    settings, parts, monkeypatch
):
    with TestClient(create_app(settings, parts)) as client:
        for language, wrong, missing in (
            ("en", "wrong password", "not authenticated"),
            ("de", "Falsches Passwort", "Nicht angemeldet"),
        ):
            pick(parts, monkeypatch, language)
            assert client.post("/api/auth/login", json={"password": "no"}).json() == {
                "detail": wrong
            }
            assert client.get("/api/settings").json() == {"detail": missing}


def test_too_many_login_attempts_keep_their_retry_after_header(settings, parts, monkeypatch):
    pick(parts, monkeypatch, "de")
    with TestClient(create_app(settings, parts)) as client:
        answers = [client.post("/api/auth/login", json={"password": "no"}) for _ in range(20)]
    limited = next(answer for answer in answers if answer.status_code == 429)
    assert limited.json() == {"detail": "Zu viele Versuche"}
    assert int(limited.headers["Retry-After"]) > 0


def test_a_stale_version_says_so_in_the_picked_language_and_still_carries_the_version(
    signed_in, parts, monkeypatch
):
    current = signed_in.get("/api/settings").json()["version"]
    pick(parts, monkeypatch, "de")

    response = signed_in.put("/api/settings", json={}, headers={"If-Match": "stale"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Die Einstellungen haben sich geändert, seit du diese Version gelesen hast; "
        "es wurde nichts gespeichert",
        "version": current,
    }
    assert response.headers["ETag"] == f'"{current}"'


def test_an_oversized_body_is_refused_in_the_picked_language_before_it_is_read(
    signed_in, parts, monkeypatch
):
    body = b"[" + b" " * 450_000 + b"]"
    english = signed_in.put("/api/settings", content=body)
    pick(parts, monkeypatch, "de")
    german = signed_in.put("/api/settings", content=body)

    assert english.status_code == german.status_code == 413
    assert english.json()["detail"] == "the body is over 400,000 bytes; the body is not accepted"
    assert (
        german.json()["detail"] == "Der Inhalt hat mehr als 400.000 Bytes; er wird nicht angenommen"
    )


def test_a_body_over_the_character_limit_names_it_with_the_languages_separator(
    signed_in, parts, monkeypatch
):
    body = {"hosts": {"a": {"rules": ["x" * 400] * 300}}}
    pick(parts, monkeypatch, "de")
    response = signed_in.put("/api/settings", json=body, headers=settings_headers(signed_in))
    assert response.status_code == 413
    assert response.json()["detail"] == "Der Inhalt hat mehr als 100.000 Zeichen"


# --- pydantic's messages, from the error's type ---


def settings_put(client: TestClient, body: dict):
    return client.put("/api/settings", json=body, headers=settings_headers(client))


def test_a_value_over_its_limit_reads_with_the_limit_in_the_picked_language(
    signed_in, parts, monkeypatch
):
    body = {"thresholds": {"repeated_deaths": 101}}
    english = settings_put(signed_in, body).json()
    pick(parts, monkeypatch, "de")
    german = settings_put(signed_in, body).json()

    assert english["detail"] == [
        {
            "type": "less_than_equal",
            "loc": ["thresholds", "repeated_deaths"],
            "msg": "Input should be less than or equal to 100",
        }
    ]
    assert german["detail"] == [
        {
            "type": "less_than_equal",
            "loc": ["thresholds", "repeated_deaths"],
            "msg": "Die Eingabe darf höchstens 100 sein",
        }
    ]


def test_a_value_under_its_limit_and_an_unknown_field_read_in_german(signed_in, parts, monkeypatch):
    pick(parts, monkeypatch, "de")
    answer = settings_put(signed_in, {"thresholds": {"disk_percent": 1, "nope": 1}}).json()
    assert {(e["type"], e["msg"]) for e in answer["detail"]} == {
        ("greater_than_equal", "Die Eingabe muss mindestens 50.0 sein"),
        ("extra_forbidden", "Zusätzliche Angaben sind nicht erlaubt"),
    }


def test_a_literal_lists_its_choices_in_the_picked_language(signed_in, parts, monkeypatch):
    english = settings_put(signed_in, {"models": {"provider": "nobody"}}).json()["detail"]
    pick(parts, monkeypatch, "de")
    german = settings_put(signed_in, {"models": {"provider": "nobody"}}).json()["detail"]

    assert [e["type"] for e in german] == ["literal_error"]
    assert english[0]["msg"] == "Input should be 'openrouter', 'anthropic' or 'local'"
    assert german[0]["msg"] == "Die Eingabe muss 'openrouter', 'anthropic' oder 'local' sein"


def test_a_validator_of_bleeps_own_reads_in_german_and_keeps_its_field(
    signed_in, parts, monkeypatch
):
    english = settings_put(signed_in, {"general": {"timezone": "Mars/Base"}}).json()["detail"]
    pick(parts, monkeypatch, "de")
    german = settings_put(signed_in, {"general": {"timezone": "Mars/Base"}}).json()["detail"]

    assert [(e["type"], e["loc"]) for e in english] == [("time_zone", ["general", "timezone"])]
    assert english[0]["msg"] == "Value error, must be an IANA time zone, e.g. Europe/Vienna"
    assert german[0]["loc"] == ["general", "timezone"]
    assert german[0]["msg"] == "muss eine IANA-Zeitzone sein, zum Beispiel Europe/Vienna"


def test_the_sentence_of_a_refused_list_entry_reads_in_german(signed_in, parts, monkeypatch):
    body = {"hosts": {"a": {"services": {"x": {"exits": ["*", "job"]}}}}}
    pick(parts, monkeypatch, "de")
    detail = settings_put(signed_in, body).json()["detail"]
    assert [e["msg"] for e in detail] == [
        '"*" steht in exits allein: Es nennt schon jeden Container'
    ]


def test_fastapis_own_422_reads_in_german_with_its_other_fields_as_before(
    signed_in, parts, monkeypatch
):
    english = signed_in.post("/api/talk/messages", json={}).json()["detail"]
    pick(parts, monkeypatch, "de")
    german = signed_in.post("/api/talk/messages", json={}).json()["detail"]

    assert english[0]["msg"] == "Field required"
    assert german[0]["msg"] == "Pflichtfeld"
    assert {k: v for k, v in german[0].items() if k != "msg"} == {
        k: v for k, v in english[0].items() if k != "msg"
    }
    assert german[0]["loc"] == ["body", "text"]


def test_an_empty_fact_and_a_text_too_short_read_in_german(signed_in, parts, monkeypatch):
    pick(parts, monkeypatch, "de")
    short = signed_in.post("/api/talk/messages", json={"text": ""}).json()["detail"]
    assert short[0]["msg"] == "Der Text muss mindestens 1 Zeichen lang sein"
    assert signed_in.post("/api/talk/messages", json={"text": "x" * 5000}).status_code == 422


def test_a_quiet_request_names_what_is_wrong_in_the_picked_language(signed_in, parts, monkeypatch):
    path = "/api/quiet/hosts/a"
    body = {"mode": "snooze"}
    english = signed_in.put(path, json=body).json()["detail"]
    pick(parts, monkeypatch, "de")
    german = signed_in.put(path, json=body).json()["detail"]

    assert english == [
        {"type": "quiet_until_needed", "loc": ["body", "until"], "msg": "a snooze needs an until"}
    ]
    assert german == [
        {
            "type": "quiet_until_needed",
            "loc": ["body", "until"],
            "msg": "Eine Pause braucht ein until",
        }
    ]


def test_a_host_form_names_the_field_and_never_what_was_sent(signed_in, parts, monkeypatch):
    pick(parts, monkeypatch, "de")
    body = {"address": "http://secret-host", "port": 70000}
    detail = signed_in.post("/api/settings/hosts/host-key", json=body).json()["detail"]
    assert {(tuple(e["loc"]), e["msg"]) for e in detail} == {
        (("address",), "muss ein Hostname oder eine IP-Adresse sein, etwa box.lan oder 10.0.0.5"),
        (("port",), "Die Eingabe darf höchstens 65535 sein"),
    }
    assert "secret-host" not in str(detail)


# --- the 422 list, names from the request, bad braces in an override ---


def test_a_422_list_logs_no_warning_in_either_language(signed_in, parts, monkeypatch, caplog):
    for language in ("en", "de"):
        pick(parts, monkeypatch, language)
        caplog.clear()
        with caplog.at_level("WARNING", logger="bleep.api"):
            answer = settings_put(signed_in, {"thresholds": {"repeated_deaths": 101}})
            assert answer.status_code == 422
            assert signed_in.post("/api/talk/messages", json={}).status_code == 422
        assert caplog.records == []


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_host_name_from_the_body_is_shown_escaped_not_raw(
    signed_in, parts, monkeypatch, language
):
    key = "a'\x01‮b"
    pick(parts, monkeypatch, language)
    answer = settings_put(signed_in, {"hosts": {key: {"may_sleep": True}}})
    assert answer.status_code == 422
    detail = answer.json()["detail"]
    sentence = "no host {} in the hosts file or the dashboard"
    german = "Kein Host {} in der Hosts-Datei oder im Dashboard"
    assert detail == (sentence if language == "en" else german).format(repr(key))
    assert "\x01" not in detail and "‮" not in detail


@pytest.mark.parametrize("language", ["en", "de"])
def test_a_protected_entry_without_a_slug_is_shown_escaped(signed_in, parts, monkeypatch, language):
    pick(parts, monkeypatch, language)
    body = {"hosts": {"a": {"services": {"x": {"protected_containers": ["\U0001f680'"]}}}}}
    detail = settings_put(signed_in, body).json()["detail"]
    start = "host a, service x: protected entry " if language == "en" else "Host a, Dienst x: "
    assert detail.startswith(start)
    assert repr("\U0001f680'") in detail


@pytest.mark.parametrize("text", ["a { b", "{x!r}", "{0}"])
def test_a_dev_panel_override_with_bad_braces_is_refused_in_german(
    signed_in, parts, monkeypatch, text
):
    pick(parts, monkeypatch, "de")
    listed = signed_in.get("/api/dev/prompts").json()
    version = next(item["version"] for item in listed if item["name"] == "llm/json_retry")
    answer = signed_in.put(
        "/api/dev/prompts/llm/json_retry", json={"text": text}, headers={"If-Match": version}
    )
    assert answer.status_code == 422
    assert answer.json()["detail"].startswith(("Ungültige Klammern", "Erlaubt sind nur"))
