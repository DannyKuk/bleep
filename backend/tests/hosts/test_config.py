from pathlib import Path

import pytest
from pydantic import SecretStr

from bleep.hosts.config import (
    HostsFileError,
    SecretMissing,
    SecretRef,
    load_hosts_file,
    resolve_secret,
)

CONFIG = Path(__file__).resolve().parents[3] / "config"
EXAMPLE = CONFIG / "hosts.example.yaml"
DEMO = CONFIG / "hosts.demo.yaml"

SSH = "{id: a, name: A, kind: ssh, address: h, user: u, key_file: /k}"


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "hosts.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_hosts_file_parses_example():
    hf = load_hosts_file(EXAMPLE)
    assert {h.kind for h in hf.hosts} == {"ssh"}
    assert [h.id for h in hf.hosts] == ["macbook", "coolify-1"]
    assert hf.hosts[1].services["shop"].urls[0].expect_status == 200


def test_demo_file_parses():
    (host,) = load_hosts_file(DEMO).hosts
    assert (host.id, host.kind, host.endpoint) == ("demo", "socket", "tcp://socket-proxy:2375")


def test_example_and_demo_kinds_cover_both():
    kinds = {h.kind for f in (EXAMPLE, DEMO) for h in load_hosts_file(f).hosts}
    assert kinds == {"ssh", "socket"}


def test_secret_values_rejected_inline(tmp_path):
    text = f"hosts: [{SSH}]\ngithub: {{token: ghp_abc}}\n"
    with pytest.raises(HostsFileError, match="token"):
        load_hosts_file(write(tmp_path, text))


def test_secret_refs_accepted(tmp_path):
    text = f"hosts: [{SSH}]\ngithub: {{token: {{env: GH}}}}\n"
    assert load_hosts_file(write(tmp_path, text)).github.token == SecretRef(env="GH")


def test_secret_ref_needs_exactly_one_source(tmp_path):
    text = f"hosts: [{SSH}]\ngithub: {{token: {{env: GH, file: /x}}}}\n"
    with pytest.raises(HostsFileError, match="token"):
        load_hosts_file(write(tmp_path, text))


def test_resolve_secret_from_env_and_file(monkeypatch, tmp_path):
    monkeypatch.setenv("BLEEP_T", "from-env")
    secret = resolve_secret(SecretRef(env="BLEEP_T"))
    assert isinstance(secret, SecretStr) and secret.get_secret_value() == "from-env"
    path = tmp_path / "tok"
    path.write_text("from-file\n")
    assert resolve_secret(SecretRef(file=path)).get_secret_value() == "from-file"
    monkeypatch.delenv("BLEEP_T")
    with pytest.raises(SecretMissing, match="BLEEP_T"):
        resolve_secret(SecretRef(env="BLEEP_T"))
    with pytest.raises(SecretMissing, match="nope"):
        resolve_secret(SecretRef(file=tmp_path / "nope"))


def test_duplicate_host_ids_rejected(tmp_path):
    with pytest.raises(HostsFileError, match="duplicate"):
        load_hosts_file(write(tmp_path, f"hosts: [{SSH}, {SSH}]"))


@pytest.mark.parametrize(
    ("host", "missing"),
    [
        ("{id: a, name: A, kind: ssh, user: u, key_file: /k}", "address"),
        ("{id: a, name: A, kind: ssh, address: h, key_file: /k}", "user"),
        ("{id: a, name: A, kind: ssh, address: h, user: u}", "key_file"),
        ("{id: a, name: A, kind: socket}", "endpoint"),
    ],
)
def test_kind_requires_its_fields(tmp_path, host, missing):
    with pytest.raises(HostsFileError, match=missing):
        load_hosts_file(write(tmp_path, f"hosts: [{host}]"))


def test_socket_host_rejects_ssh_fields_and_bad_endpoint(tmp_path):
    with pytest.raises(HostsFileError, match="address"):
        load_hosts_file(
            write(
                tmp_path, "hosts: [{id: a, name: A, kind: socket, endpoint: unix:///s, address: h}]"
            )
        )
    with pytest.raises(HostsFileError, match="endpoint"):
        load_hosts_file(
            write(tmp_path, "hosts: [{id: a, name: A, kind: socket, endpoint: http://x}]")
        )


def test_unknown_keys_rejected(tmp_path):
    with pytest.raises(HostsFileError, match="bogus"):
        load_hosts_file(write(tmp_path, f"hosts: [{SSH}]\nbogus: 1"))
    with pytest.raises(HostsFileError, match="bogus"):
        load_hosts_file(
            write(
                tmp_path, "hosts: [{id: a, name: A, kind: socket, endpoint: unix:///s, bogus: 1}]"
            )
        )


def test_unreadable_or_invalid_yaml_is_a_hosts_file_error(tmp_path):
    with pytest.raises(HostsFileError, match="missing.yaml"):
        load_hosts_file(tmp_path / "missing.yaml")
    with pytest.raises(HostsFileError):
        load_hosts_file(write(tmp_path, "hosts: [unclosed"))
