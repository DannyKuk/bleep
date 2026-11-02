import logging
import shlex
import stat
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest

from bleep.hosts.config import HostConfig
from bleep.hosts.ssh import OUTPUT_CAP, CommandResult, SshSession, SshUnavailable
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
    assert session.connected


async def test_address_is_lowercased_for_the_known_hosts_lookup(connect):
    sess = SshSession(ssh_host(address="MacBook-Pro-M1.local"), connect=connect)
    await sess.open()
    assert connect.calls[0][0] == ("macbook-pro-m1.local",)
    await sess.close()


async def test_no_known_hosts_file_skips_host_key_check_with_a_warning(connect, caplog):
    sess = SshSession(ssh_host(known_hosts_file=None), connect=connect)
    with caplog.at_level(logging.WARNING, logger="bleep.hosts.ssh"):
        await sess.open()
    assert connect.calls[0][1]["known_hosts"] is None
    assert any("host key" in r.getMessage() for r in caplog.records)
    await sess.close()


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


async def test_run_caps_stdout_and_stderr(connect, session):
    big = "x" * (OUTPUT_CAP + 5000)
    connect.connections[0].reply = SimpleNamespace(exit_status=1, stdout=big, stderr=big)
    result = await session.run(["top"])
    assert result.exit_code == 1
    assert len(result.stdout) <= OUTPUT_CAP + 100
    assert len(result.stderr) <= OUTPUT_CAP + 100
    assert "cut" in result.stdout


async def test_run_times_out(connect, session):
    connect.connections[0].run_delay = 1.0
    with pytest.raises(TimeoutError):
        await session.run(["top"], timeout=0.05)


async def test_run_on_a_lost_connection_is_ssh_unavailable(connect, session):
    connect.connections[0].lose()
    with pytest.raises(SshUnavailable):
        await session.run(["uname", "-s"])


async def test_run_failing_mid_command_is_ssh_unavailable(connect, session):
    connect.connections[0].run_error = asyncssh.ConnectionLost("gone")
    with pytest.raises(SshUnavailable):
        await session.run(["uname", "-s"])


def test_session_is_for_ssh_hosts_only():
    socket_host = HostConfig(
        id="d", name="D", kind="socket", endpoint="unix:///var/run/docker.sock"
    )
    with pytest.raises(ValueError):
        SshSession(socket_host)
