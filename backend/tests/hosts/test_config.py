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
    assert [h.id for h in hf.hosts] == ["mac", "coolify-1"]
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


@pytest.mark.parametrize("url", ["http://host:abc/", "shop.example/health", "ftp://x.example/"])
def test_a_bad_service_url_is_a_config_error_naming_the_host_and_the_url(tmp_path, url):
    host = (
        "{id: a, name: A, kind: socket, endpoint: 'unix:///s', "
        f"services: {{shop: {{urls: [{{url: '{url}'}}]}}}}}}"
    )
    with pytest.raises(HostsFileError) as error:
        load_hosts_file(write(tmp_path, f"hosts: [{host}]\n"))
    assert "host 'a'" in str(error.value) and url in str(error.value)


def with_docs_paths(tmp_path, paths: str) -> Path:
    service = f"{{shop: {{repo: acme/shop, docs_paths: {paths}}}}}"
    return write(tmp_path, f"hosts: [{SSH[:-1]}, services: {service}}}]\n")


@pytest.mark.parametrize(
    ("given", "normal"),
    [
        ("[docs/]", ["docs"]),
        ("[./docs]", ["docs"]),
        ("['./docs/runbook.md']", ["docs/runbook.md"]),
        ("['docs//ops/./', ' ops ']", ["docs/ops", "ops"]),
        ("[docs, ./docs/, ops]", ["docs", "ops"]),
    ],
)
def test_docs_paths_are_normalised_when_the_hosts_file_loads(tmp_path, given, normal):
    (host,) = load_hosts_file(with_docs_paths(tmp_path, given)).hosts

    assert host.services["shop"].docs_paths == normal


@pytest.mark.parametrize(
    ("given", "why"),
    [
        ("['../secrets']", "inside the repo"),
        ("['docs/../../etc']", "inside the repo"),
        ("['/etc/passwd']", "relative to the repo"),
        ("['']", "names no folder or file"),
        ("['./']", "names no folder or file"),
        ("['docs\\\\ops']", "not a file path"),
    ],
)
def test_a_docs_path_outside_the_repo_or_empty_is_refused_with_its_reason(tmp_path, given, why):
    with pytest.raises(HostsFileError, match=why) as refused:
        load_hosts_file(with_docs_paths(tmp_path, given))

    assert "shop" in str(refused.value) and "docs_paths" in str(refused.value)


def test_the_readme_is_read_unless_the_service_says_not():
    services = load_hosts_file(EXAMPLE).hosts[1].services
    assert services["shop"].readme is True
    shop = load_hosts_file(DEMO).hosts[0].services["shop"]
    assert (shop.docs_paths, shop.readme) == (["demo/shop/docs"], False)


def test_a_protected_entry_with_an_empty_slug_is_warned_about(tmp_path, caplog):
    text = """
hosts:
  - {id: a, name: A, kind: ssh, address: h, user: u, key_file: /k,
      services: {shop: {protected_containers: ["🚀", "shop-db-1"]}}}
"""
    with caplog.at_level("WARNING", logger="bleep.hosts.config"):
        load_hosts_file(write(tmp_path, text))

    assert len(caplog.records) == 1
    assert "'🚀'" in caplog.text and "protect the application by its uuid" in caplog.text
