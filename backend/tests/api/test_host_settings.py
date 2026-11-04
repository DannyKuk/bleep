"""Hosts added, changed and removed on the dashboard (#194): trust on first use made explicit, a
key bound to its connection, keys never coming back out, the hosts file winning, and every change
in force without a restart. No network, SSH server or Docker engine: a fake host-key scan and a
connector that hands out fake engines, reading the keys the way the real one does."""

import asyncio
import concurrent.futures
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
NEW_CLIENT_KEY = asyncssh.generate_private_key("ssh-ed25519")
NEW_PEM = NEW_CLIENT_KEY.export_private_key().decode()
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


BOX_IP, OTHER_IP = "192.168.1.10", "10.0.0.6"


class Resolver:
    """Name lookups without the network; a name not listed does not resolve."""

    def __init__(self) -> None:
        self.answers = {"box.lan": [BOX_IP], "other.lan": [OTHER_IP]}

    async def __call__(self, host: str, port: int) -> list[str]:
        if host not in self.answers:
            raise OSError("no such name")
        return self.answers[host]


class Scan:
    """Stands in for asyncssh.get_server_host_key: the key each address offers, by the IP it is
    asked at (the name is resolved and pinned before)."""

    def __init__(self) -> None:
        self.keys = {(BOX_IP, 22): SERVER_KEY, (BOX_IP, 2222): SERVER_KEY}
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


def start(settings, scan, connector, resolver=None):
    host = socket_host(
        id="a", services={"compose:shop": ServiceOverrides(protected_containers=["shop-web-1"])}
    )
    parts = build_parts(
        settings,
        Registry([host], connector),
        FakeEmbedder(),
        plugins=[],
        scan=scan,
        resolver=resolver or Resolver(),
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


def public(private_pem: str | None) -> bytes | None:
    """The public half of a stored key: Bleep stores its own export of what was pasted, whose
    check bytes differ at each export."""
    return asyncssh.import_private_key(private_pem).public_data if private_pem else None


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
    assert scan.asked == [(BOX_IP, 22)]
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
    assert public(key) == CLIENT_KEY.public_data
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
    assert connector.used[-1][:2] == ("box.lan", "deploy")
    assert public(connector.used[-1][2]) == CLIENT_KEY.public_data


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
        assert public(connector.used[0][2]) == CLIENT_KEY.public_data


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
    scan.keys[(OTHER_IP, 22)] = OTHER_SERVER_KEY
    offered = offer(app, "other.lan")
    host_key = {"offer": offered["offer"], "fingerprint": offered["fingerprint"]}
    response = edit(app, address="other.lan", ssh_key=NEW_PEM, host_key=host_key)
    assert response.status_code == 200, response.text
    assert public(stored_keys(app)[0]) == NEW_CLIENT_KEY.public_data
    assert OTHER_SERVER_KEY.export_public_key().decode().split()[1] in stored_keys(app)[1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and connector.used[-1][0] != "other.lan":
        time.sleep(0.02)
    assert connector.used[-1][:2] == ("other.lan", "deploy")
    assert public(connector.used[-1][2]) == NEW_CLIENT_KEY.public_data


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
    assert (public(stored_keys(app)[0]), stored_keys(app)[1]) == (CLIENT_KEY.public_data, line)
    assert edit(app, ssh_key=NEW_PEM).status_code == 200
    assert (public(stored_keys(app)[0]), stored_keys(app)[1]) == (NEW_CLIENT_KEY.public_data, line)
    assert host_view(app)["name"] == "Box two"


def test_a_host_key_rotated_on_the_server_is_confirmed_again_without_touching_the_key(app, scan):
    assert add(app).status_code == 201
    scan.keys[(BOX_IP, 22)] = OTHER_SERVER_KEY
    offered = offer(app)
    host_key = {"offer": offered["offer"], "fingerprint": offered["fingerprint"]}
    assert edit(app, host_key=host_key).status_code == 200
    key, line = stored_keys(app)
    assert public(key) == CLIENT_KEY.public_data
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
        stored_body = stored_keys(app)[0].splitlines()[2]  # Bleep's own export of the key
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
    for secret in (KEY_BODY, NEW_KEY_BODY, KEY_BODY[::-1], HOST_KEY_B64, stored_body):
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


# -- fix round 1 ------------------------------------------------------------------------------


def test_only_the_key_itself_is_stored_whatever_was_pasted_around_it(app):
    pasted = "hello, here is the key\n" + PEM + "\nand a note after it\n"
    assert add(app, ssh_key=pasted).status_code == 201
    key, _ = stored_keys(app)
    assert key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert key.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")
    assert "hello" not in key and "note" not in key
    assert public(key) == CLIENT_KEY.public_data


def test_a_paste_holding_two_keys_is_refused(app):
    response = add(app, ssh_key=PEM + NEW_PEM)
    assert response.status_code == 422 and "one private key" in response.json()["detail"]
    assert host_view(app) is None


def test_an_unknown_field_is_never_named_in_a_422(app):
    body = new_host(app) | {PEM: "x"}
    response = app.post("/api/settings/hosts", json=body)
    assert response.status_code == 422
    assert KEY_BODY not in response.text and "BEGIN" not in response.text
    assert response.json()["detail"][0]["loc"] == ["<unknown field>"]


def test_a_non_ascii_version_is_stale_not_a_crash(app):
    assert add(app).status_code == 201
    view = host_view(app)
    body = {"name": "Box", "address": "box.lan", "user": "deploy"}
    response = app.put("/api/settings/hosts/c", json=body, headers={"If-Match": "versión".encode()})
    assert response.status_code == 409
    assert (
        app.delete("/api/settings/hosts/c", headers={"If-Match": "versión".encode()}).status_code
        == 409
    )
    assert host_view(app) == view


def test_a_form_body_is_a_422_not_a_crash(app):
    response = app.post(
        "/api/settings/hosts", content=b"id=c", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("name", ["SSH_KEY:c", "KNOWN_HOSTS:c"])
def test_a_hosts_keys_cannot_be_set_or_cleared_through_the_secrets_routes(app, name):
    assert add(app).status_code == 201
    before = stored_keys(app)
    assert app.put(f"/api/secrets/{name}", json={"value": "x"}).status_code == 404
    assert app.delete(f"/api/secrets/{name}").status_code == 404
    assert stored_keys(app) == before


@pytest.mark.parametrize(
    "address", ["fe80::1%x ssh-ed25519 AAAA", "fe80::1%x,*", "fe80::1%eth0"], ids=repr
)
def test_an_ipv6_scope_id_is_refused(app, scan, address):
    response = app.post("/api/settings/hosts/host-key", json={"address": address})
    assert response.status_code == 422 and scan.asked == []


# -- the host-key fetch is not a scanner (O1) -------------------------------------------------

GENERIC = "Could not get an SSH host key from that address."


@pytest.mark.parametrize(
    ("address", "answers"),
    [
        ("127.0.0.1", None),
        ("::1", None),
        ("169.254.169.254", None),
        ("::ffff:127.0.0.1", None),
        ("0.0.0.0", None),
        ("fe80::1", None),
        ("224.0.0.1", None),
        ("localhost", ["127.0.0.1"]),
        ("metadata.lan", ["169.254.169.254"]),
        ("mixed.lan", ["192.168.1.10", "127.0.0.1"]),
        ("mapped.lan", ["::ffff:169.254.169.254"]),
        ("nat64.lan", ["64:ff9b::7f00:1"]),
        ("6to4.lan", ["2002:7f00:1::1"]),
    ],
)
def test_no_host_key_is_fetched_from_loopback_link_local_or_their_ipv6_forms(
    settings, scan, connector, address, answers
):
    resolver = Resolver()
    if answers is not None:
        resolver.answers[address] = answers
    with TestClient(create_app(settings, start(settings, scan, connector, resolver))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.post("/api/settings/hosts/host-key", json={"address": address})
        assert response.status_code == 502 and response.json()["detail"] == GENERIC
    assert scan.asked == []


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "172.16.4.2", "192.168.1.10", "100.64.0.7", "fd00::5"]
)
def test_a_host_on_a_private_network_is_allowed(app, scan, address):
    scan.keys[(address, 22)] = SERVER_KEY
    assert offer(app, address)["fingerprint"] == SERVER_KEY.get_fingerprint("sha256")
    assert scan.asked == [(address, 22)]


def test_the_name_is_resolved_once_and_the_checked_address_is_the_one_asked(
    settings, scan, connector
):
    class Rebinding(Resolver):
        """Public the first time, loopback after: the fetch must use the first answer only."""

        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def __call__(self, host, port):
            self.calls += 1
            return [BOX_IP] if self.calls == 1 else ["127.0.0.1"]

    resolver = Rebinding()
    with TestClient(create_app(settings, start(settings, scan, connector, resolver))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        client.parts = client.app.state.parts  # type: ignore[attr-defined]
        offered = offer(client, "flip.lan")
        assert scan.asked == [(BOX_IP, 22)]
        # Saving checks the name again: it now resolves to loopback, so nothing is saved.
        body = {
            "id": "c",
            "name": "Box",
            "address": "flip.lan",
            "user": "deploy",
            "ssh_key": PEM,
            "host_key": {"offer": offered["offer"], "fingerprint": offered["fingerprint"]},
        }
        response = client.post("/api/settings/hosts", json=body)
        assert response.status_code == 422
        assert host_view(client) is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError("[Errno 61] Connect call failed ('192.168.1.10', 22)"),
        asyncssh.ProtocolNotSupported("Unsupported SSH version"),
        ConnectionResetError("Connection lost"),
        TimeoutError(),
        None,
    ],
    ids=["closed", "not ssh", "reset", "silent", "no key"],
)
def test_every_failure_to_fetch_says_the_same_sentence(app, scan, failure, caplog):
    async def failing(host, port, **kwargs):
        if failure is not None:
            raise failure
        return None

    app.parts.dashboard_hosts.scan = failing
    response = app.post("/api/settings/hosts/host-key", json={"address": "box.lan"})
    assert response.status_code == 502
    assert response.json() == {"detail": GENERIC}
    assert "Connect call failed" not in caplog.text and "192.168.1.10" not in caplog.text


def test_a_name_that_does_not_resolve_says_the_same_sentence(app):
    response = app.post("/api/settings/hosts/host-key", json={"address": "nowhere.lan"})
    assert response.status_code == 502 and response.json() == {"detail": GENERIC}


def test_one_fetch_at_a_time_and_a_second_is_a_429(app, scan):
    release = app.portal.call(asyncio.Event)
    entered = app.portal.call(asyncio.Event)

    async def slow(host, port, **kwargs):
        entered.set()
        await release.wait()
        return SERVER_KEY

    app.parts.dashboard_hosts.scan = slow
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        first = pool.submit(app.post, "/api/settings/hosts/host-key", json={"address": "box.lan"})
        app.portal.call(entered.wait)
        second = app.post("/api/settings/hosts/host-key", json={"address": "box.lan"})
        assert second.status_code == 429
        app.portal.call(release.set)
        assert first.result(timeout=5).status_code == 200


# -- fix round 2 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [ValueError("x"), EOFError(), RuntimeError("y"), asyncio.IncompleteReadError(b"", 4)],
    ids=["ValueError", "EOFError", "RuntimeError", "IncompleteReadError"],
)
def test_an_unexpected_failure_of_the_scan_says_the_same_sentence_and_frees_the_fetch(app, failure):
    async def failing(host, port, **kwargs):
        raise failure

    app.parts.dashboard_hosts.scan = failing
    response = app.post("/api/settings/hosts/host-key", json={"address": "box.lan"})
    assert response.status_code == 502 and response.json() == {"detail": GENERIC}
    app.parts.dashboard_hosts.scan = Scan()
    assert app.post("/api/settings/hosts/host-key", json={"address": "box.lan"}).status_code == 200


@pytest.mark.parametrize("answer", [ValueError("x"), ["not-an-ip"]], ids=["raises", "not an ip"])
def test_a_resolver_that_misbehaves_says_the_same_sentence(settings, scan, connector, answer):
    class Odd(Resolver):
        async def __call__(self, host, port):
            if isinstance(answer, Exception):
                raise answer
            return answer

    with TestClient(create_app(settings, start(settings, scan, connector, Odd()))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.post("/api/settings/hosts/host-key", json={"address": "box.lan"})
        assert response.status_code == 502 and response.json() == {"detail": GENERIC}
    assert scan.asked == []


def test_a_hung_name_lookup_ends_the_fetch_and_frees_it(settings, scan, connector, monkeypatch):
    monkeypatch.setattr("bleep.hosts.ssh.FETCH_TIMEOUT_S", 0.05)

    class Hung(Resolver):
        async def __call__(self, host, port):
            if host == "slow.lan":
                await asyncio.Event().wait()
            return await super().__call__(host, port)

    with TestClient(create_app(settings, start(settings, scan, connector, Hung()))) as client:
        client.post("/api/auth/login", json={"password": "test-password"})
        response = client.post("/api/settings/hosts/host-key", json={"address": "slow.lan"})
        assert response.status_code == 502 and response.json() == {"detail": GENERIC}
        follow = client.post("/api/settings/hosts/host-key", json={"address": "box.lan"})
        assert follow.status_code == 200  # not a 429: the hung lookup let go of the lock
