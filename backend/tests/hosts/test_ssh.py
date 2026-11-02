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
    sess = SshSession(ssh_host(address="MacBook-Pro-M1.local"), connect=connect)
    await sess.open()
    assert connect.calls[0][0] == ("macbook-pro-m1.local",)
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
