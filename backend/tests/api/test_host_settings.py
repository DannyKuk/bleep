"""Hosts added, changed and removed on the dashboard (#194): trust on first use made explicit, a
key bound to its connection, keys never coming back out, the hosts file winning, and every change
in force without a restart. No network, SSH server or Docker engine: a fake host-key scan and a
connector that hands out fake engines, reading the keys the way the real one does."""

import logging
import time

import asyncssh
import pytest
from fastapi.testclient import TestClient

from bleep.hosts.config import ServiceOverrides
from bleep.hosts.dashboard import dashboard_keys
from bleep.hosts.registry import Registry
from bleep.hosts.ssh import SshUnavailable
from bleep.main import create_app
from bleep.memory.embeddings import FakeEmbedder
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Signal, Subject
from bleep.wiring import build_parts
from fakes import FakeEngine, socket_host

SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
OTHER_SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
CLIENT_KEY = asyncssh.generate_private_key("ssh-ed25519")
PEM = CLIENT_KEY.export_private_key().decode()
NEW_PEM = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()
# Fragments that must never come back: the private keys' bodies and the host key's base64.
KEY_BODY = PEM.splitlines()[2]
NEW_KEY_BODY = NEW_PEM.splitlines()[2]
HOST_KEY_B64 = SERVER_KEY.export_public_key().decode().split()[1]


def box(name: str, project: str, labels: dict[str, str] | None = None) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="img",
        state="running",
        labels={"com.docker.compose.project": project} | (labels or {}),
    )


class Scan:
    """Stands in for asyncssh.get_server_host_key: the key each address offers."""

    def __init__(self) -> None:
        self.keys = {("box.lan", 22): SERVER_KEY, ("box.lan", 2222): SERVER_KEY}
        self.asked: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int, **kwargs):
        self.asked.append((host, port))
        if (host, port) not in self.keys:
            raise OSError(f"Connect call failed ('{host}', {port})")
        return self.keys[(host, port)]


class Connector:
    """Hands each host its fake engine. A dashboard host connects only with the key and host key
    the secret file holds, as `SshSession` does, and the keys it was given are recorded."""

    def __init__(self) -> None:
        self.engines = {
            "a": FakeEngine("a", [box("shop-web-1", "shop")]),
            "c": FakeEngine(
                "c",
                [
                    box("blog-web-1", "blog"),
                    box("bleep-backend-1", "bleep", {"bleep.self": "true"}),
                ],
            ),
        }
        self.secrets = None
        self.used: list[tuple[str, str, str, str]] = []

    async def __call__(self, host):
        if host.source == "dashboard":
            keys = dashboard_keys(self.secrets)(host.id)
            if keys.private_key is None or keys.known_hosts is None:
                raise SshUnavailable(host.id, "no SSH key is set; enter it on the dashboard")
            self.used.append(
                (
                    host.address,
                    host.user,
                    keys.private_key.get_secret_value(),
                    keys.known_hosts.get_secret_value(),
                )
            )
        return None, self.engines[host.id]


@pytest.fixture
def scan() -> Scan:
    return Scan()


@pytest.fixture
def connector() -> Connector:
    return Connector()


def start(settings, scan, connector):
    host = socket_host(
        id="a", services={"compose:shop": ServiceOverrides(protected_containers=["shop-web-1"])}
    )
    parts = build_parts(
        settings, Registry([host], connector), FakeEmbedder(), plugins=[], scan=scan
    )
    connector.secrets = parts.secrets
    parts.notify.start = lambda runner: None  # type: ignore[method-assign]  # never polls
    return parts


@pytest.fixture
def app(settings, scan, connector):
    settings.watch_interval_s = 0.05
    parts = start(settings, scan, connector)
    with TestClient(create_app(settings, parts)) as client:
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 204
        client.parts = parts  # type: ignore[attr-defined]
        yield client


def offer(client, address: str = "box.lan", port: int = 22) -> dict:
    response = client.post("/api/settings/hosts/host-key", json={"address": address, "port": port})
    assert response.status_code == 200, response.text
    return response.json()


def new_host(client, **fields) -> dict:
    offered = offer(client, fields.get("address", "box.lan"), fields.get("port", 22))
    body = {
        "id": "c",
        "name": "Box",
        "address": "box.lan",
        "user": "deploy",
        "ssh_key": PEM,
        "host_key": {"offer": offered["offer"], "fingerprint": offered["fingerprint"]},
    }
    return body | fields


def add(client, **fields):
    return client.post("/api/settings/hosts", json=new_host(client, **fields))


def host_view(client, host_id: str = "c") -> dict | None:
    hosts = client.get("/api/settings").json()["hosts"]
    return next((h for h in hosts if h["id"] == host_id), None)


def edit(client, version: str | None = None, **fields):
    view = host_view(client)
    connection = view["connection"]
    body = {
        "name": view["name"],
        "address": connection["address"],
        "port": connection["port"],
        "user": connection["user"],
        "docker_socket": connection["docker_socket"],
    } | fields
    headers = {"If-Match": version or connection["version"]}
    return client.put("/api/settings/hosts/c", json=body, headers=headers)


def remove(client, host_id: str = "c"):
    version = host_view(client, host_id)["connection"]["version"]
    return client.delete(f"/api/settings/hosts/{host_id}", headers={"If-Match": version})


def watched(client, host_id: str = "c", timeout: float = 5.0) -> dict:
    """The host as `GET /api/hosts` shows it once the watcher has reached it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for host in client.get("/api/hosts").json():
            if host["id"] == host_id and host["reachable"]:
                return host
        time.sleep(0.02)
    raise AssertionError(f"host {host_id} was never reached")


def stored_keys(client, host_id: str = "c") -> tuple[str | None, str | None]:
    keys = dashboard_keys(client.parts.secrets)(host_id)
    return (
        keys.private_key.get_secret_value() if keys.private_key else None,
        keys.known_hosts.get_secret_value() if keys.known_hosts else None,
    )


def read_if_there(path) -> bytes:
    """A file of the data directory; SQLite's -wal and -shm files come and go while it runs."""
    try:
        return path.read_bytes() if path.name != "secrets.json" else b""
    except FileNotFoundError:
        return b""


# -- trust on first use ---------------------------------------------------------------------


def test_fetching_a_host_key_shows_its_fingerprint_and_saves_nothing(app, scan):
    offered = offer(app, "Box.LAN")
    assert offered["fingerprint"] == SERVER_KEY.get_fingerprint("sha256")
    assert offered["key_type"] == "ssh-ed25519" and offered["address"] == "box.lan"
    assert scan.asked == [("box.lan", 22)]
    assert host_view(app) is None
    assert app.parts.secrets.path.exists() is False


def test_a_host_is_saved_only_with_the_fingerprint_bleep_was_offered(app):
    wrong = new_host(app)
    wrong["host_key"]["fingerprint"] = OTHER_SERVER_KEY.get_fingerprint("sha256")
    response = app.post("/api/settings/hosts", json=wrong)
    assert response.status_code == 409 and "fingerprint" in response.json()["detail"]
    assert host_view(app) is None and stored_keys(app) == (None, None)

    response = add(app)
    assert response.status_code == 201, response.text
    connection = response.json()["connection"]
    assert connection["source"] == "dashboard" and connection["kind"] == "ssh"
    assert connection["ssh_key"]["set"] and connection["host_key"]["set"]
    key, line = stored_keys(app)
    assert key == PEM.strip()
    assert line == f"box.lan {SERVER_KEY.export_public_key().decode().strip()}"


def test_an_offer_is_used_once_and_only_for_the_address_and_port_it_was_fetched_for(app, scan):
    body = new_host(app, address="box.lan", port=22)
    elsewhere = body | {"port": 2222}
    response = app.post("/api/settings/hosts", json=elsewhere)
    assert response.status_code == 422 and "another address" in response.json()["detail"]

    assert app.post("/api/settings/hosts", json=body).status_code == 201
    again = app.post("/api/settings/hosts", json=body | {"id": "d"})
    assert again.status_code == 409 and "fetch the host key again" in again.json()["detail"]


def test_an_address_where_no_ssh_server_answers_is_a_502_and_saves_nothing(app):
    response = app.post("/api/settings/hosts/host-key", json={"address": "nothing.lan"})
    assert response.status_code == 502
    assert host_view(app) is None


@pytest.mark.parametrize(
    "address",
    ["ssh://box.lan", "deploy@box.lan", "box.lan:22", "-oProxyCommand=x", "box lan", "box/lan", ""],
)
def test_only_a_host_name_or_ip_address_is_an_address(app, scan, address):
    response = app.post("/api/settings/hosts/host-key", json={"address": address})
    assert response.status_code == 422
    assert scan.asked == []


# -- applied without a restart --------------------------------------------------------------


def test_an_added_host_is_watched_at_once_with_its_key_and_bleeps_own_containers_left_out(
    app, connector
):
    assert add(app).status_code == 201
    host = watched(app)
    assert host["name"] == "Box" and host["kind"] == "ssh"
    services = app.get("/api/services").json()
    assert [s["name"] for s in services if s["host_id"] == "c"] == ["blog"]
    assert connector.used[-1][:3] == ("box.lan", "deploy", PEM.strip())


def test_a_dashboard_hosts_services_take_rules_and_protected_containers(app):
    assert add(app).status_code == 201
    version = app.get("/api/settings").json()["version"]
    body = {
        "hosts": {"c": {"services": {"compose:blog": {"protected_containers": ["blog-web-1"]}}}}
    }
    response = app.put("/api/settings", json=body, headers={"If-Match": version})
    assert response.status_code == 200, response.text
    watched(app)
    (blog,) = [s for s in app.parts.registry.services("c") if s.name == "blog"]
    assert blog.protected == {"blog-web-1"}


def test_a_removed_host_stops_being_watched_and_its_open_incidents_close_with_a_notice(app):
    assert add(app).status_code == 201
    watched(app)
    signal = Signal(
        subject=Subject(kind="service", host_id="c", service_id="c/compose:blog"),
        kind="unhealthy",
        at="2026-11-04T10:00:00Z",
        container="blog-web-1",
    )
    incident, _ = app.portal.call(app.parts.incidents.open_or_attach, signal.subject, [signal])

    assert remove(app).status_code == 204
    assert host_view(app) is None
    assert "c" not in [h["id"] for h in app.get("/api/hosts").json()]
    assert stored_keys(app) == (None, None)
    closed = app.get(f"/api/incidents/{incident.id}").json()
    assert closed["incident"]["status"] == "closed"
    events = app.get(f"/api/incidents/{incident.id}/events").json()
    assert any("host was removed" in e["payload"].get("text", "") for e in events)


def test_a_host_added_again_under_a_removed_ones_id_starts_without_its_overrides(app):
    assert add(app).status_code == 201
    version = app.get("/api/settings").json()["version"]
    body = {"hosts": {"c": {"rules": ["never restart blog"]}}}
    assert app.put("/api/settings", json=body, headers={"If-Match": version}).status_code == 200
    assert remove(app).status_code == 204
    assert add(app).status_code == 201
    assert host_view(app)["rules"] == []


def test_dashboard_hosts_and_their_keys_come_back_after_a_restart(settings, scan):
    settings.watch_interval_s = 0.05
    with TestClient(create_app(settings, start(settings, scan, Connector()))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        client.parts = client.app.state.parts  # type: ignore[attr-defined]
        assert add(client).status_code == 201

    connector = Connector()  # the first app closed its engines when it stopped
    with TestClient(create_app(settings, start(settings, scan, connector))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        view = host_view(client)
        assert view["connection"]["source"] == "dashboard" and view["connection"]["ssh_key"]["set"]
        watched(client)
        assert connector.used[0][2] == PEM.strip()


# -- a key is bound to its connection -------------------------------------------------------


@pytest.mark.parametrize(
    "change", [{"address": "other.lan"}, {"user": "root"}, {"port": 2200}], ids=str
)
def test_a_new_address_port_or_user_clears_the_key_and_the_host_key(app, change):
    assert add(app).status_code == 201
    response = edit(app, **change)
    assert response.status_code == 200, response.text
    connection = response.json()["connection"]
    assert not connection["ssh_key"]["set"] and not connection["host_key"]["set"]
    assert stored_keys(app) == (None, None)
    (state,) = [h for h in app.parts.registry.hosts() if h.config.id == "c"]
    assert (state.config.address, state.config.user, state.config.port) == (
        change.get("address", "box.lan"),
        change.get("user", "deploy"),
        change.get("port", 22),
    )


def test_a_new_address_with_its_own_key_and_confirmed_host_key_connects_with_those(
    app, scan, connector
):
    assert add(app).status_code == 201
    scan.keys[("other.lan", 22)] = OTHER_SERVER_KEY
    offered = offer(app, "other.lan")
    host_key = {"offer": offered["offer"], "fingerprint": offered["fingerprint"]}
    response = edit(app, address="other.lan", ssh_key=NEW_PEM, host_key=host_key)
    assert response.status_code == 200, response.text
    assert stored_keys(app)[0] == NEW_PEM.strip()
    assert OTHER_SERVER_KEY.export_public_key().decode().split()[1] in stored_keys(app)[1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and connector.used[-1][0] != "other.lan":
        time.sleep(0.02)
    assert connector.used[-1][:3] == ("other.lan", "deploy", NEW_PEM.strip())


def test_a_key_sent_for_an_older_version_of_the_host_is_not_stored(app):
    assert add(app).status_code == 201
    typed_for = host_view(app)["connection"]["version"]
    assert edit(app, address="other.lan").status_code == 200  # another tab moved the host

    response = edit(app, version=typed_for, address="other.lan", ssh_key=NEW_PEM)
    assert response.status_code == 409
    assert response.json()["version"] == host_view(app)["connection"]["version"]
    assert stored_keys(app) == (None, None)

    view = host_view(app)
    body = {"name": "Box", "address": "other.lan", "user": "deploy", "ssh_key": NEW_PEM}
    assert app.put("/api/settings/hosts/c", json=body).status_code == 428
    assert view == host_view(app)


def test_a_new_name_or_key_alone_keeps_the_connection_and_the_other_key(app):
    assert add(app).status_code == 201
    _, line = stored_keys(app)
    assert edit(app, name="Box two").status_code == 200
    assert stored_keys(app) == (PEM.strip(), line)
    assert edit(app, ssh_key=NEW_PEM).status_code == 200
    assert stored_keys(app) == (NEW_PEM.strip(), line)
    assert host_view(app)["name"] == "Box two"


def test_a_host_key_rotated_on_the_server_is_confirmed_again_without_touching_the_key(app, scan):
    assert add(app).status_code == 201
    scan.keys[("box.lan", 22)] = OTHER_SERVER_KEY
    offered = offer(app)
    host_key = {"offer": offered["offer"], "fingerprint": offered["fingerprint"]}
    assert edit(app, host_key=host_key).status_code == 200
    key, line = stored_keys(app)
    assert key == PEM.strip()
    assert OTHER_SERVER_KEY.export_public_key().decode().split()[1] in line


# -- the hosts file wins ----------------------------------------------------------------------


def test_a_hosts_file_host_is_shown_as_file_managed_and_cannot_be_changed_or_removed(app):
    view = host_view(app, "a")
    assert view["connection"]["source"] == "file" and view["connection"]["version"] is None
    body = {"name": "A", "address": "evil.lan", "user": "root"}
    response = app.put("/api/settings/hosts/a", json=body, headers={"If-Match": "x"})
    assert response.status_code == 409 and "hosts file" in response.json()["detail"]
    response = app.delete("/api/settings/hosts/a", headers={"If-Match": "x"})
    assert response.status_code == 409
    response = add(app, id="a")
    assert response.status_code == 409
    assert [h.config.source for h in app.parts.registry.hosts() if h.config.id == "a"] == ["file"]


def test_the_dashboard_still_cannot_unprotect_what_the_hosts_file_protects(app):
    version = app.get("/api/settings").json()["version"]
    body = {"hosts": {"a": {"services": {"compose:shop": {"protected_containers": []}}}}}
    assert app.put("/api/settings", json=body, headers={"If-Match": version}).status_code == 200
    assert host_view(app, "a")["services"]["compose:shop"]["protected_containers"] == ["shop-web-1"]


@pytest.mark.parametrize(
    "extra",
    [
        {"kind": "socket"},
        {"endpoint": "tcp://socket-proxy:2375"},
        {"key_file": "/run/secrets/ssh/id"},
        {"known_hosts_file": "/etc/passwd"},
        {"source": "file"},
    ],
    ids=str,
)
def test_a_socket_host_or_a_file_path_cannot_come_from_the_dashboard(app, extra):
    response = app.post("/api/settings/hosts", json=new_host(app) | extra)
    assert response.status_code == 422
    assert host_view(app) is None


# -- keys never come back -------------------------------------------------------------------


def test_no_key_or_host_key_ever_comes_back_from_an_api_a_log_or_the_database(
    app, settings, scan, caplog
):
    with caplog.at_level(logging.DEBUG):
        assert add(app).status_code == 201
        watched(app)
        assert edit(app, ssh_key=NEW_PEM).status_code == 200
        bad = edit(app, ssh_key=PEM.replace(KEY_BODY, KEY_BODY[::-1]))
        assert bad.status_code == 422
        answers = [bad.text]
        for path in (
            "/api/settings",
            "/api/hosts",
            "/api/services",
            "/api/secrets",
            "/api/incidents",
        ):
            answers.append(app.get(path).text)
        answers.append(remove(app).text)

    database = b"".join(read_if_there(path) for path in settings.data_dir.iterdir())
    for secret in (KEY_BODY, NEW_KEY_BODY, KEY_BODY[::-1], HOST_KEY_B64):
        assert all(secret not in answer for answer in answers)
        assert secret not in caplog.text
        assert secret.encode() not in database


@pytest.mark.parametrize(
    "bad_key",
    [
        "not a key at all",
        SERVER_KEY.export_public_key().decode(),
        CLIENT_KEY.export_private_key("pkcs8-pem", passphrase="pw").decode(),
        PEM.replace("\n", "\n\x1b[2J", 1),
    ],
    ids=["text", "public key", "encrypted", "control character"],
)
def test_a_key_that_is_not_an_unencrypted_private_key_is_refused_without_quoting_it(app, bad_key):
    response = add(app, ssh_key=bad_key)
    assert response.status_code == 422
    assert max(bad_key.splitlines(), key=len) not in response.text
    assert host_view(app) is None and stored_keys(app) == (None, None)


def test_an_invalid_body_is_refused_without_its_values(app):
    body = new_host(app, user="-oops", ssh_key=["a", PEM])
    response = app.post("/api/settings/hosts", json=body)
    assert response.status_code == 422
    assert KEY_BODY not in response.text and "-oops" not in response.text


def test_the_routes_need_a_session(settings, scan, connector):
    with TestClient(create_app(settings, start(settings, scan, connector))) as client:
        assert (
            client.post("/api/settings/hosts/host-key", json={"address": "box.lan"}).status_code
            == 401
        )
        assert client.post("/api/settings/hosts", json={}).status_code == 401
        assert client.delete("/api/settings/hosts/c").status_code == 401
