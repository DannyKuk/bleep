import asyncio
import os
import shlex
import shutil
import socket
import stat
import tempfile
import time
from pathlib import Path

import asyncssh
import pytest
from pydantic import SecretStr

from bleep.hosts import ssh as ssh_module
from bleep.hosts.config import HostConfig
from bleep.hosts.ssh import (
    OUTPUT_CAP,
    CommandResult,
    SshSession,
    SshUnavailable,
    sweep_stale_forward_dirs,
)
from fakes import FakeSshConnect, ssh_host


@pytest.fixture
def connect() -> FakeSshConnect:
    return FakeSshConnect()


@pytest.fixture
async def session(connect):
    sess = SshSession(ssh_host(), connect=connect)
    connect.session_ref["session"] = sess
    await sess.open()
    yield sess
    await sess.close()


async def test_open_connects_with_key_known_hosts_and_keepalive(connect, session):
    ((args, kwargs),) = connect.calls
    assert args == ("m1.local",)
    assert kwargs["port"] == 2222
    assert kwargs["username"] == "danny"
    assert kwargs["client_keys"] == ["/keys/id_ed25519"]
    assert kwargs["known_hosts"] == "/keys/known_hosts"
    assert kwargs["keepalive_interval"] == 15
    assert kwargs["keepalive_count_max"] == 3
    assert kwargs["config"] == []  # hosts.yaml alone decides, not ~/.ssh/config
    assert kwargs["agent_path"] is None
    assert session.connected


async def test_address_is_lowercased_for_the_known_hosts_lookup(connect):
    sess = SshSession(ssh_host(address="Mac.local"), connect=connect)
    await sess.open()
    assert connect.calls[0][0] == ("mac.local",)
    await sess.close()


async def test_no_known_hosts_file_verifies_against_the_users_known_hosts(
    connect, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "known_hosts").write_text("m1.local ssh-ed25519 AAAA\n")
    sess = SshSession(ssh_host(known_hosts_file=None), connect=connect)
    await sess.open()
    # Left out, asyncssh checks the key against ~/.ssh/known_hosts; None would turn checking off.
    assert "known_hosts" not in connect.calls[0][1]
    await sess.close()


async def test_no_known_hosts_anywhere_refuses_to_connect(connect, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sess = SshSession(ssh_host(known_hosts_file=None), connect=connect)
    with pytest.raises(SshUnavailable, match="known_hosts"):
        await sess.open()
    assert connect.calls == []


async def test_forwards_the_remote_socket_to_a_private_local_socket(connect, session):
    (listener,) = connect.connections[0].listeners
    assert listener.dest_path == "/Users/danny/.docker/run/docker.sock"
    assert Path(listener.listen_path) == session.forwarded_socket
    directory = session.forwarded_socket.parent
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


async def test_close_sets_the_stop_flag_before_closing_and_removes_the_socket_dir(connect, session):
    directory = session.forwarded_socket.parent
    await session.close()
    conn = connect.connections[0]
    assert conn.closed and conn.stopped_when_closed is True
    assert conn.listeners[0].closed
    assert not directory.exists()
    assert session.stopped and not session.connected
    with pytest.raises(SshUnavailable):
        _ = session.forwarded_socket


async def test_ensure_open_is_a_no_op_while_connected(connect, session):
    await session.ensure_open()
    await session.ensure_open()
    assert len(connect.calls) == 1


async def test_ensure_open_rebuilds_connection_and_forward_after_a_lost_connection(
    connect, session
):
    old_socket = session.forwarded_socket
    connect.connections[0].lose()
    assert not session.connected
    with pytest.raises(SshUnavailable):
        _ = session.forwarded_socket

    await session.ensure_open()

    assert len(connect.calls) == 2
    assert session.connected
    assert session.forwarded_socket != old_socket
    assert not old_socket.parent.exists()
    assert connect.connections[1].listeners[0].dest_path == "/Users/danny/.docker/run/docker.sock"


async def test_ensure_open_after_a_deliberate_close_refuses(connect, session):
    await session.close()
    with pytest.raises(SshUnavailable):
        await session.ensure_open()
    assert len(connect.calls) == 1


async def test_open_failure_is_ssh_unavailable(connect):
    connect.error = OSError("connection refused")
    sess = SshSession(ssh_host(), connect=connect)
    with pytest.raises(SshUnavailable) as caught:
        await sess.open()
    assert caught.value.host_id == "m1"
    assert not sess.connected


async def test_rejected_host_key_is_ssh_unavailable(connect):
    connect.error = asyncssh.HostKeyNotVerifiable("host key mismatch")
    sess = SshSession(ssh_host(), connect=connect)
    with pytest.raises(SshUnavailable):
        await sess.open()


async def test_run_quotes_every_argument(connect, session):
    result = await session.run(["echo", "a b; rm -rf /", "$(id)"])
    command = connect.connections[0].commands[0]
    assert command == shlex.join(["echo", "a b; rm -rf /", "$(id)"])
    assert shlex.split(command) == ["echo", "a b; rm -rf /", "$(id)"]
    assert result == CommandResult(exit_code=0, stdout="ok\n", stderr="")


@pytest.mark.parametrize("argv", ["df -P", ("df", "-P"), [], ["df", 1]])
async def test_run_refuses_anything_but_a_list_of_strings(session, argv):
    with pytest.raises(TypeError):
        await session.run(argv)


async def test_run_reads_bytes_and_decodes_with_replacement(connect, session):
    conn = connect.connections[0]
    conn.output = {"stdout": b"ok \xff\n", "stderr": b"warn\n", "exit_status": 3}
    result = await session.run(["df", "-P"])
    assert result == CommandResult(exit_code=3, stdout="ok \ufffd\n", stderr="warn\n")
    assert conn.process_kwargs[0]["encoding"] is None
    (process,) = conn.processes
    assert process.closed and not process.terminated


async def test_run_caps_output_in_bytes_and_ends_the_process(connect, session):
    conn = connect.connections[0]
    big = b"x" * (OUTPUT_CAP + 5000)
    conn.output = {"stdout": big, "stderr": b"", "exit_status": 0}
    conn.hang = True  # still printing when the cap is reached
    result = await session.run(["top"], timeout=2)
    assert result.stdout.startswith("x" * 1000) and "cut" in result.stdout
    assert len(result.stdout.encode()) <= OUTPUT_CAP + 100
    assert result.exit_code is None
    (process,) = conn.processes
    assert process.terminated and process.closed


async def test_run_timeout_terminates_and_closes_the_process(connect, session):
    conn = connect.connections[0]
    conn.hang = True  # a process that never finishes, like `sleep 30`
    with pytest.raises(TimeoutError):
        await session.run(["sleep", "30"], timeout=0.05)
    (process,) = conn.processes
    assert process.terminated and process.closed


async def test_run_on_a_lost_connection_is_ssh_unavailable(connect, session):
    connect.connections[0].lose()
    with pytest.raises(SshUnavailable):
        await session.run(["uname", "-s"])


async def test_run_failing_mid_command_is_ssh_unavailable(connect, session):
    connect.connections[0].error = asyncssh.ConnectionLost("gone")
    with pytest.raises(SshUnavailable):
        await session.run(["uname", "-s"])
    assert connect.connections[0].processes[0].closed


def test_session_is_for_ssh_hosts_only():
    socket_host = HostConfig(
        id="d", name="D", kind="socket", endpoint="unix:///var/run/docker.sock"
    )
    with pytest.raises(ValueError):
        SshSession(socket_host)


@pytest.fixture
def forward_root(monkeypatch):
    # Short on purpose: a unix socket path is limited to about 104 bytes on macOS.
    root = Path(tempfile.mkdtemp(prefix="bt-", dir="/tmp"))
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def aged(path: Path) -> Path:
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_sweep_removes_stale_forward_dirs_only(forward_root):
    dead = forward_root / "bleep-ssh-dead"
    dead.mkdir()
    (dead / "docker.sock").touch()  # left behind by a crash: nothing listens on it
    aged(dead)
    empty = forward_root / "bleep-ssh-empty"
    empty.mkdir()
    aged(empty)
    live = forward_root / "bleep-ssh-live"
    live.mkdir()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(live / "docker.sock"))
    listener.listen()
    aged(live)
    fresh = forward_root / "bleep-ssh-fresh"  # another session may be setting this one up
    fresh.mkdir()
    other = forward_root / "unrelated"
    other.mkdir()
    aged(other)
    try:
        sweep_stale_forward_dirs()
    finally:
        listener.close()
    assert not dead.exists() and not empty.exists()
    assert live.exists() and fresh.exists() and other.exists()


async def test_open_sweeps_stale_forward_dirs(forward_root, connect):
    stale = forward_root / "bleep-ssh-stale"
    stale.mkdir()
    aged(stale)
    sess = SshSession(ssh_host(), connect=connect)
    await sess.open()
    assert not stale.exists()
    assert sess.forwarded_socket.parent.exists()  # its own dir is new and kept
    await sess.close()


def test_sweep_keeps_dirs_of_live_sessions_in_this_process(forward_root, monkeypatch):
    mine = forward_root / "bleep-ssh-mine"
    mine.mkdir()
    aged(mine)
    monkeypatch.setattr(ssh_module, "_LIVE_DIRS", {mine})
    sweep_stale_forward_dirs()
    assert mine.exists()


async def test_a_hung_connect_times_out_so_open_and_close_cannot_block(monkeypatch):
    async def hangs(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(ssh_module, "CONNECT_TIMEOUT_S", 0.02)
    monkeypatch.setattr(ssh_module, "CONNECT_GRACE_S", 0.02)
    sess = SshSession(ssh_host(), connect=hangs)
    with pytest.raises(SshUnavailable, match="TimeoutError"):
        await asyncio.wait_for(sess.open(), 2)
    await asyncio.wait_for(sess.close(), 2)  # the lock was released


# -- a host added on the dashboard: keys from the secret file (#194) ----------------------------

SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
CLIENT_KEY = asyncssh.generate_private_key("ssh-ed25519")
CLIENT_PEM = CLIENT_KEY.export_private_key().decode()


M1_IP = "192.168.1.10"


async def lan(host, port):
    return [M1_IP]


def dashboard_host(**overrides):
    return ssh_host(source="dashboard", key_file=None, known_hosts_file=None, **overrides)


def keys(private_key: str | None = CLIENT_PEM, known_hosts: str | None = None):
    line = known_hosts or ssh_module.known_hosts_line("m1.local", 2222, SERVER_KEY)

    def read(host_id: str) -> ssh_module.DashboardKeys:
        assert host_id == "m1"
        return ssh_module.DashboardKeys(
            private_key=SecretStr(private_key) if private_key else None,
            known_hosts=SecretStr(line) if known_hosts != "" else None,
        )

    return read


async def test_a_dashboard_host_connects_with_its_key_and_only_its_confirmed_host_key(
    connect, monkeypatch, tmp_path
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # a ~/.ssh/known_hosts must not matter
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "known_hosts").write_text("m1.local ssh-ed25519 AAAAother\n")
    sess = SshSession(dashboard_host(), connect=connect, keys=keys(), resolve=lan)
    await sess.open()

    ((args, kwargs),) = connect.calls
    assert args == (M1_IP,)  # the vetted address, pinned
    (key,) = kwargs["client_keys"]
    assert key.public_data == CLIENT_KEY.public_data
    trusted, cas, revoked = kwargs["known_hosts"]
    assert [k.public_data for k in trusted] == [SERVER_KEY.public_data] and cas == revoked == []
    assert kwargs["config"] == [] and kwargs["agent_path"] is None
    await sess.close()


async def test_a_dashboard_host_key_rotated_since_it_was_confirmed_is_refused(connect):
    connect.error = asyncssh.HostKeyNotVerifiable("Host key is not trusted for host m1.local")
    sess = SshSession(dashboard_host(), connect=connect, keys=keys(), resolve=lan)
    with pytest.raises(SshUnavailable, match="not trusted"):
        await sess.open()


@pytest.mark.parametrize(
    ("private_key", "known_hosts", "why"),
    [
        (None, None, "no SSH key is set"),
        (CLIENT_PEM, "", "no host key is confirmed"),
    ],
)
async def test_a_dashboard_host_without_its_key_or_host_key_never_connects(
    connect, private_key, known_hosts, why
):
    sess = SshSession(
        dashboard_host(), connect=connect, keys=keys(private_key, known_hosts), resolve=lan
    )
    with pytest.raises(SshUnavailable, match=why):
        await sess.open()
    assert connect.calls == []


async def test_a_dashboard_host_with_no_key_reader_never_falls_back_to_a_file(connect):
    sess = SshSession(dashboard_host(), connect=connect)
    with pytest.raises(SshUnavailable, match="no SSH key"):
        await sess.open()
    assert connect.calls == []


async def test_an_unreadable_dashboard_key_is_refused_without_quoting_it(connect):
    garbage = "-----BEGIN OPENSSH PRIVATE KEY-----\nsk-or-v1-NOT-A-KEY-0123456789\n-----END"
    sess = SshSession(dashboard_host(), connect=connect, keys=keys(garbage), resolve=lan)
    with pytest.raises(SshUnavailable) as refused:
        await sess.open()
    assert "cannot be read" in str(refused.value)
    assert "NOT-A-KEY" not in str(refused.value)
    assert refused.value.__cause__ is None and refused.value.__suppress_context__
    assert connect.calls == []


async def test_the_dashboard_key_is_read_again_at_every_connect(connect):
    current = {"pem": CLIENT_PEM}
    line = ssh_module.known_hosts_line("m1.local", 2222, SERVER_KEY)

    def read(_host_id):
        return ssh_module.DashboardKeys(SecretStr(current["pem"]), SecretStr(line))

    sess = SshSession(dashboard_host(), connect=connect, keys=read, resolve=lan)
    await sess.open()
    other = asyncssh.generate_private_key("ssh-ed25519")
    current["pem"] = other.export_private_key().decode()
    connect.connections[-1].lose()
    await sess.ensure_open()
    assert connect.calls[1][1]["client_keys"][0].public_data == other.public_data
    await sess.close()


def test_the_known_hosts_line_names_the_port_unless_it_is_22():
    plain = ssh_module.known_hosts_line("Box.Local", 22, SERVER_KEY)
    assert plain.startswith("box.local ssh-ed25519 ")
    odd = ssh_module.known_hosts_line("box.local", 2222, SERVER_KEY)
    assert odd.startswith("[box.local]:2222 ssh-ed25519 ")
    assert asyncssh.import_known_hosts(odd).match("box.local", "", 2222)[0]
    assert not asyncssh.import_known_hosts(odd).match("box.local", "", 22)[0]


async def test_fetching_a_host_key_asks_the_lowercased_address_without_the_users_config():
    asked = []

    async def scan(host, port, **kwargs):
        asked.append((host, port, kwargs))
        return SERVER_KEY

    looked_up = []

    async def resolve(host, port):
        looked_up.append(host)
        return [M1_IP]

    key = await ssh_module.fetch_host_key("Box.Local", 2222, scan, resolve)
    assert key is SERVER_KEY
    assert looked_up == ["box.local"]
    assert asked == [(M1_IP, 2222, {"config": []})]


@pytest.mark.parametrize(
    "outcome", [OSError("Connect call failed"), asyncssh.DisconnectError(2, "bye"), None]
)
async def test_fetching_a_host_key_from_no_server_is_ssh_unavailable(outcome):
    async def scan(host, port, **kwargs):
        if outcome is not None:
            raise outcome
        return None

    with pytest.raises(SshUnavailable):
        await ssh_module.fetch_host_key("box.local", 22, scan, lan)


async def test_fetching_a_host_key_from_a_server_that_hangs_times_out(monkeypatch):
    monkeypatch.setattr(ssh_module, "FETCH_TIMEOUT_S", 0.01)

    async def scan(host, port, **kwargs):
        await asyncio.Event().wait()

    with pytest.raises(SshUnavailable):
        await ssh_module.fetch_host_key("box.local", 22, scan, lan)


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["::1"], ["169.254.169.254"], [M1_IP, "127.0.0.1"], ["::ffff:7f00:1"]],
    ids=str,
)
async def test_a_dashboard_host_whose_name_now_resolves_to_loopback_is_never_connected(
    connect, answers
):
    async def rebound(host, port):
        return answers

    sess = SshSession(dashboard_host(), connect=connect, keys=keys(), resolve=rebound)
    with pytest.raises(SshUnavailable, match="loopback, link-local"):
        await sess.open()
    assert connect.calls == []


async def test_a_dashboard_host_is_resolved_again_at_every_connect(connect):
    answers = [[M1_IP], ["10.0.0.9"]]

    async def moving(host, port):
        return answers.pop(0)

    sess = SshSession(dashboard_host(), connect=connect, keys=keys(), resolve=moving)
    await sess.open()
    connect.connections[-1].lose()
    await sess.ensure_open()
    assert [args for args, _ in connect.calls] == [(M1_IP,), ("10.0.0.9",)]
    await sess.close()


async def test_a_hosts_file_host_is_neither_resolved_nor_pinned(connect):
    async def never(host, port):
        raise AssertionError("a hosts file host is connected by its name, as before")

    sess = SshSession(ssh_host(), connect=connect, resolve=never)
    await sess.open()
    assert connect.calls[0][0] == ("m1.local",)
    await sess.close()


@pytest.mark.parametrize(
    ("address", "refused"),
    [
        ("127.0.0.53", True),
        ("0.1.2.3", True),
        ("169.254.169.254", True),
        ("fe80::1%eth0", True),
        ("ff02::1", True),
        ("::", True),
        ("::ffff:127.0.0.1", True),
        ("::127.0.0.1", True),
        ("2001:0:4136:e378:8000:63bf:80ff:fffe", True),  # Teredo carrying 127.0.0.1
        ("10.1.2.3", False),
        ("172.20.0.4", False),
        ("192.168.0.2", False),
        ("100.100.1.1", False),
        ("fd12::1", False),
        ("93.184.216.34", False),
        ("2606:4700::1111", False),
    ],
)
def test_which_addresses_a_dashboard_host_may_never_have(address, refused):
    assert ssh_module.is_refused(address) is refused


# -- fix round 2: the name lookup is bounded, and more addresses are refused ---------------------


async def hung(host, port):
    await asyncio.Event().wait()


async def test_a_hung_name_lookup_ends_the_host_key_fetch_at_its_timeout(monkeypatch):
    monkeypatch.setattr(ssh_module, "FETCH_TIMEOUT_S", 0.01)

    async def scan(host, port, **kwargs):
        raise AssertionError("never asked: the name never resolved")

    with pytest.raises(SshUnavailable):
        await ssh_module.fetch_host_key("box.lan", 22, scan, hung)


async def test_a_hung_name_lookup_ends_a_connect_at_its_outer_bound(connect, monkeypatch):
    monkeypatch.setattr(ssh_module, "CONNECT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(ssh_module, "CONNECT_GRACE_S", 0)
    sess = SshSession(dashboard_host(), connect=connect, keys=keys(), resolve=hung)
    with pytest.raises(SshUnavailable):
        await sess.open()
    assert connect.calls == []
    await sess.close()  # the session lock was released


@pytest.mark.parametrize(
    "answer",
    [ValueError("bad answer"), ["not-an-ip"], [object()]],
    ids=["raises", "not an ip", "not text"],
)
async def test_a_resolver_that_misbehaves_counts_as_no_answer(connect, answer):
    async def odd(host, port):
        if isinstance(answer, Exception):
            raise answer
        return answer

    with pytest.raises(ssh_module.AddressRefused):
        await ssh_module.vetted_address("box.lan", 22, odd)


@pytest.mark.parametrize(
    "address",
    [
        "100.100.100.200",  # Alibaba Cloud metadata, inside CGNAT
        "fd00:ec2::254",  # AWS's IPv6 metadata, inside ULA
        "64:ff9b:1::a00:5",  # local-use NAT64 (RFC 8215)
        "::ffff:0:7f00:1",  # SIIT
        "::ffff:0:a00:5",
    ],
)
def test_the_other_cloud_metadata_and_translation_addresses_are_refused(address):
    assert ssh_module.is_refused(address)


@pytest.mark.parametrize("address", ["100.100.100.199", "100.64.0.1", "fd00:ec2::253"])
def test_their_neighbours_on_the_private_networks_stay_allowed(address):
    assert not ssh_module.is_refused(address)
