import contextlib
import socket
import threading

import pytest

from tests import network_guard
from tests.network_guard import NetworkUsed, take_violations


def test_a_name_lookup_for_another_machine_is_refused_and_reported():
    with pytest.raises(NetworkUsed, match="api.smith.langchain.com"):
        socket.getaddrinfo("api.smith.langchain.com", 443)
    assert take_violations() == ["name lookup of 'api.smith.langchain.com'"]


def test_a_connect_to_another_machine_is_refused_and_reported():
    with socket.socket() as sock, pytest.raises(NetworkUsed):
        sock.connect(("203.0.113.9", 443))
    assert take_violations() == ["connect to '203.0.113.9'"]


def test_a_background_thread_cannot_hide_it_the_report_is_what_fails_the_test():
    def lookup() -> None:
        with contextlib.suppress(NetworkUsed):  # a library thread that logs and goes on
            socket.getaddrinfo("cloud.example.test", 443)

    thread = threading.Thread(target=lookup)
    thread.start()
    thread.join()
    assert take_violations() == ["name lookup of 'cloud.example.test'"]


def test_loopback_and_unix_sockets_keep_working():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.socket() as client:
            client.connect(server.getsockname())
    left, right = socket.socketpair()
    left.close()
    right.close()
    socket.getaddrinfo("localhost", 80)
    assert take_violations() == []


@pytest.mark.network
def test_a_test_marked_network_may_look_a_name_up(monkeypatch):
    """The opt-in real-model test downloads from the hub; the lookup goes through to the stub."""
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 443))]
    monkeypatch.setattr(network_guard, "_real_getaddrinfo", lambda *args, **kwargs: answer)
    assert socket.getaddrinfo("huggingface.co", 443) == answer
    assert take_violations() == []


def test_the_network_is_closed_again_for_the_next_test():
    assert not network_guard._open
