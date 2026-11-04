"""The secret file: written only by the backend's user, never read back through anything but
`get`, the env wins over it, and a plugin's address is bound to its token (#135)."""

import json
import os
import stat

import pytest

from bleep.store.secrets import (
    SecretFamily,
    SecretFileError,
    SecretFromEnv,
    SecretName,
    SecretNotSaved,
    SecretRefused,
    SecretStore,
    UnknownSecret,
)

TOKEN = "ghp_a-real-looking-token-value"
NAMES = (
    SecretName("GITHUB_TOKEN"),
    SecretName("TELEGRAM_CHAT_ID", kind="id"),
    SecretName("COOLIFY_URL", kind="address", carries="COOLIFY_TOKEN"),
    SecretName("COOLIFY_TOKEN"),
)


def store_at(tmp_path, env=None) -> SecretStore:
    return SecretStore(tmp_path / "data" / "secrets.json", NAMES, env=env or {})


async def set_token(store: SecretStore, value: str) -> set[str]:
    """The token typed for the address as it is now."""
    return await store.set(
        "COOLIFY_TOKEN", value, address_version=store.address_version("COOLIFY_URL")
    )


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


async def test_a_secret_set_from_the_dashboard_is_read_back_and_survives_a_restart(tmp_path):
    store = store_at(tmp_path)
    assert await store.set("GITHUB_TOKEN", f"  {TOKEN}\n") == {"GITHUB_TOKEN"}

    for reading in (store, store_at(tmp_path)):
        secret = reading.get("GITHUB_TOKEN")
        assert secret is not None and secret.get_secret_value() == TOKEN
        assert reading.state("GITHUB_TOKEN").source == "dashboard"


async def test_the_file_is_mode_0600_and_no_temporary_file_is_left(tmp_path):
    before = os.umask(0)  # the widest umask: the file must still be private
    try:
        store = store_at(tmp_path)
        await store.set("GITHUB_TOKEN", TOKEN)
        await store.set("GITHUB_TOKEN", TOKEN + "2")
    finally:
        os.umask(before)

    path = tmp_path / "data" / "secrets.json"
    assert mode(path) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["secrets.json"]


async def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_one(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    await store.set("GITHUB_TOKEN", TOKEN)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        await store.set("GITHUB_TOKEN", "other")
    path = tmp_path / "data" / "secrets.json"
    assert json.loads(path.read_text())["values"] == {"GITHUB_TOKEN": TOKEN}
    assert [p.name for p in path.parent.iterdir()] == ["secrets.json"]
    assert store.get("GITHUB_TOKEN").get_secret_value() == TOKEN  # memory stays as stored


def test_a_file_readable_by_others_is_made_private_on_load(tmp_path, caplog):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "values": {"GITHUB_TOKEN": TOKEN}}))
    path.chmod(0o644)

    store = store_at(tmp_path)
    assert mode(path) == 0o600
    assert store.get("GITHUB_TOKEN") is not None
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    "content",
    [f"not json {TOKEN}", json.dumps([TOKEN]), json.dumps({"values": {"GITHUB_TOKEN": 1}})],
)
def test_an_unreadable_file_refuses_to_start_without_quoting_it(tmp_path, content):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir()
    path.write_text(content)

    with pytest.raises(SecretFileError) as refused:
        store_at(tmp_path)
    assert TOKEN not in str(refused.value) and str(path) in str(refused.value)


def test_a_name_no_longer_known_is_dropped_on_load(tmp_path):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "values": {"OLD_TOKEN": TOKEN}}))
    assert store_at(tmp_path).get("OLD_TOKEN") is None


async def test_the_env_wins_and_cannot_be_changed_or_cleared_from_the_dashboard(tmp_path):
    store = store_at(tmp_path, env={"GITHUB_TOKEN": "from-env"})
    assert store.get("GITHUB_TOKEN").get_secret_value() == "from-env"
    state = store.state("GITHUB_TOKEN")
    assert state.set and state.source == "env" and state.env_owned

    with pytest.raises(SecretFromEnv, match="GITHUB_TOKEN"):
        await store.set("GITHUB_TOKEN", TOKEN)
    with pytest.raises(SecretFromEnv):
        await store.clear("GITHUB_TOKEN")
    assert not (tmp_path / "data" / "secrets.json").exists()


async def test_an_empty_env_variable_counts_as_unset(tmp_path):
    store = store_at(tmp_path, env={"GITHUB_TOKEN": ""})
    await store.set("GITHUB_TOKEN", TOKEN)
    assert store.state("GITHUB_TOKEN").source == "dashboard"


async def test_clearing_removes_the_value_from_the_file(tmp_path):
    store = store_at(tmp_path)
    await store.set("GITHUB_TOKEN", TOKEN)
    assert await store.clear("GITHUB_TOKEN") == {"GITHUB_TOKEN"}
    assert await store.clear("GITHUB_TOKEN") == set()  # nothing to clear: nothing changed
    assert store.get("GITHUB_TOKEN") is None and not store.state("GITHUB_TOKEN").set
    assert TOKEN not in (tmp_path / "data" / "secrets.json").read_text()


async def test_setting_the_same_value_again_changes_nothing(tmp_path):
    store = store_at(tmp_path)
    await store.set("GITHUB_TOKEN", TOKEN)
    assert await store.set("GITHUB_TOKEN", TOKEN) == set()


@pytest.mark.parametrize("value", ["", "   ", "a\nb", "a\x00b", "a\x1b[0mb", "x" * 16_385])
async def test_a_value_that_is_empty_too_long_or_holds_control_characters_is_refused(
    tmp_path, value
):
    store = store_at(tmp_path)
    with pytest.raises(SecretRefused) as refused:
        await store.set("GITHUB_TOKEN", value)
    assert value.strip() == "" or value not in str(refused.value)


async def test_an_unknown_name_is_refused(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(UnknownSecret):
        await store.set("BLEEP_SECRET", "x")
    with pytest.raises(UnknownSecret):
        store.state("BLEEP_ADMIN_PASSWORD")


async def test_changing_an_address_clears_the_token_bound_to_it(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await set_token(store, TOKEN)

    changed = await store.set("COOLIFY_URL", "https://elsewhere.example.com")

    assert changed == {"COOLIFY_URL", "COOLIFY_TOKEN"}
    assert store.get("COOLIFY_TOKEN") is None
    assert TOKEN not in (tmp_path / "data" / "secrets.json").read_text()
    assert store_at(tmp_path).get("COOLIFY_TOKEN") is None


async def test_the_same_address_again_keeps_the_token(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await set_token(store, TOKEN)
    assert await store.set("COOLIFY_URL", "https://coolify.example.com ") == set()
    assert store.get("COOLIFY_TOKEN") is not None


async def test_clearing_an_address_clears_its_token(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await set_token(store, TOKEN)
    assert await store.clear("COOLIFY_URL") == {"COOLIFY_URL", "COOLIFY_TOKEN"}
    assert store.get("COOLIFY_TOKEN") is None


async def test_a_token_needs_its_address_first(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(SecretRefused, match="COOLIFY_URL first"):
        await set_token(store, TOKEN)


@pytest.mark.parametrize(
    "env",
    [{"COOLIFY_TOKEN": "env-token"}, {"COOLIFY_URL": "https://env.example.com"}],
)
async def test_an_address_and_its_token_come_whole_from_the_env_when_it_sets_either(tmp_path, env):
    """Never an env token sent to a dashboard address, nor the other way round."""
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir()
    values = {"COOLIFY_URL": "https://dashboard.example.com", "COOLIFY_TOKEN": TOKEN}
    path.write_text(json.dumps({"version": 1, "values": values}))

    store = store_at(tmp_path, env=env)
    for name in ("COOLIFY_URL", "COOLIFY_TOKEN"):
        got = store.get(name)
        assert (got.get_secret_value() if got else None) == env.get(name)
        assert store.state(name).env_owned
        with pytest.raises(SecretFromEnv):
            await store.set(name, "https://x.example.com")
    assert store.environ().get("COOLIFY_URL") == env.get("COOLIFY_URL")


async def test_environ_reads_the_values_in_force_and_the_env_for_other_names(tmp_path):
    store = store_at(tmp_path, env={"OTHER": "1"})
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    view = store.environ()
    assert view["COOLIFY_URL"] == "https://coolify.example.com"
    assert view.get("COOLIFY_TOKEN") is None and view["OTHER"] == "1"
    await set_token(store, TOKEN)
    assert view["COOLIFY_TOKEN"] == TOKEN  # live, not a copy


async def test_listeners_hear_which_names_changed_and_never_the_values(tmp_path):
    store = store_at(tmp_path)
    heard: list[set[str]] = []

    async def listen(changed: frozenset[str]) -> None:
        heard.append(set(changed))

    store.subscribe(listen)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await set_token(store, TOKEN)
    await set_token(store, TOKEN)  # unchanged: nobody is told
    await store.clear("COOLIFY_URL")
    assert heard == [{"COOLIFY_URL"}, {"COOLIFY_TOKEN"}, {"COOLIFY_URL", "COOLIFY_TOKEN"}]


async def test_a_failing_listener_does_not_undo_the_change_or_stop_the_others(tmp_path, caplog):
    store = store_at(tmp_path)
    heard = []

    async def broken(changed):
        raise RuntimeError(f"echoes {TOKEN}")

    async def listen(changed):
        heard.append(changed)

    store.subscribe(broken)
    store.subscribe(listen)
    await store.set("GITHUB_TOKEN", TOKEN)
    assert store.get("GITHUB_TOKEN") is not None and heard
    assert TOKEN not in caplog.text and "RuntimeError" in caplog.text


async def test_nothing_shows_a_value(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await set_token(store, TOKEN)
    assert TOKEN not in repr(store) and TOKEN not in str(store.state("COOLIFY_TOKEN"))
    assert TOKEN not in repr(store.get("COOLIFY_TOKEN"))


async def test_a_value_something_needs_can_be_changed_but_not_cleared(tmp_path):
    needed = SecretName("LANGSMITH_API_KEY", required="TRACING=langsmith needs it")
    store = SecretStore(tmp_path / "secrets.json", [needed], env={})
    await store.set("LANGSMITH_API_KEY", "lsv2_first")
    assert await store.set("LANGSMITH_API_KEY", "lsv2_second") == {"LANGSMITH_API_KEY"}
    with pytest.raises(SecretRefused, match="TRACING=langsmith needs it"):
        await store.clear("LANGSMITH_API_KEY")
    assert store.get("LANGSMITH_API_KEY") is not None


def test_the_tracing_keys_tracing_needs_are_marked_required_and_the_login_is_not_listed():
    from bleep.plugins.coolify.plugin import CoolifyPlugin
    from bleep.plugins.mcp_url import McpUrlPlugin
    from bleep.wiring import secret_names

    names = {n.name: n for n in secret_names([CoolifyPlugin(), McpUrlPlugin()], "langfuse")}
    assert names["LANGFUSE_SECRET_KEY"].required and names["LANGFUSE_PUBLIC_KEY"].required
    assert names["LANGSMITH_API_KEY"].required is None
    assert names["COOLIFY_URL"].carries == "COOLIFY_TOKEN"
    assert names["MCP_URL"].carries == "MCP_TOKEN" and names["MCP_TOKEN"].kind == "secret"
    assert not {"BLEEP_ADMIN_PASSWORD", "BLEEP_SECRET", "LOCAL_BASE_URL"} & set(names)


def test_a_link_at_the_file_is_refused_and_its_target_left_alone(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("{}")
    victim.chmod(0o644)
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir()
    path.symlink_to(victim)

    with pytest.raises(SecretFileError, match="link"):
        store_at(tmp_path)
    assert mode(victim) == 0o644


def test_a_directory_at_the_file_is_refused(tmp_path):
    (tmp_path / "data" / "secrets.json").mkdir(parents=True)
    with pytest.raises(SecretFileError):
        store_at(tmp_path)


def test_a_temporary_file_left_by_a_killed_write_is_removed_at_load(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    leftover = data / ".secrets-abc123.tmp"
    leftover.write_text(json.dumps({"version": 1, "values": {"GITHUB_TOKEN": TOKEN}}))
    store_at(tmp_path)
    assert not leftover.exists()


@pytest.mark.parametrize("char", ["\x85", "\x9f", " ", " ", "​", "﻿"])
async def test_c1_controls_line_separators_and_invisible_characters_are_refused(tmp_path, char):
    with pytest.raises(SecretRefused):
        await store_at(tmp_path).set("GITHUB_TOKEN", f"abc{char}def")


async def test_a_failure_before_the_write_writes_nothing_and_tells_no_one(tmp_path):
    store = store_at(tmp_path)
    heard = []

    async def prepare(changed):
        raise RuntimeError(f"echoes {TOKEN}")

    async def listen(changed):
        heard.append(changed)

    store.subscribe(prepare, before_write=True)
    store.subscribe(listen)
    with pytest.raises(SecretNotSaved) as not_saved:
        await store.set("GITHUB_TOKEN", TOKEN)
    assert TOKEN not in str(not_saved.value)
    assert store.get("GITHUB_TOKEN") is None and heard == []
    assert not (tmp_path / "data" / "secrets.json").exists()


async def test_a_listener_before_the_write_runs_before_the_file_changes(tmp_path):
    store = store_at(tmp_path)
    seen_on_disk = []

    async def prepare(changed):
        path = tmp_path / "data" / "secrets.json"
        seen_on_disk.append(path.read_text() if path.exists() else None)

    store.subscribe(prepare, before_write=True)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    assert seen_on_disk == [None]


async def test_an_address_version_is_opaque_and_changes_with_the_address(tmp_path):
    store = store_at(tmp_path)
    assert store.address_version("COOLIFY_URL") is None
    await store.set("COOLIFY_URL", "https://coolify.example.com/path-secret")
    first = store.address_version("COOLIFY_URL")
    await store.set("COOLIFY_URL", "https://other.example.com")
    assert first and "path-secret" not in first and first != store.address_version("COOLIFY_URL")
    assert store.address_version("GITHUB_TOKEN") is None


# -- per-host values: a multi-line kind and families (#194) ------------------------------------

KEYS = SecretFamily("SSH_KEY:", kind="multiline")
KNOWN = SecretFamily("KNOWN_HOSTS:")
PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def host_store(tmp_path, env=None) -> SecretStore:
    return SecretStore(
        tmp_path / "data" / "secrets.json", NAMES, families=(KEYS, KNOWN), env=env or {}
    )


async def test_a_multiline_value_keeps_its_line_feeds_and_a_pasted_crlf_becomes_lf(tmp_path):
    store = host_store(tmp_path)
    await store.replace({"SSH_KEY:m1": "\r\n" + PEM.replace("\n", "\r\n") + "\r\n"})

    for reading in (store, host_store(tmp_path)):
        assert reading.get("SSH_KEY:m1").get_secret_value() == PEM


@pytest.mark.parametrize(
    "bad",
    ["\r", "\t", "\x00", "\x1b[2J", "\x85", " ", "​", "﻿"],
    ids=repr,
)
async def test_a_multiline_value_refuses_every_control_but_the_line_feed(tmp_path, bad):
    store = host_store(tmp_path)
    with pytest.raises(SecretRefused) as refused:
        await store.replace({"SSH_KEY:m1": PEM.replace("\n", "\n" + bad, 1)})
    assert "b3Blbn" not in str(refused.value)
    assert store.get("SSH_KEY:m1") is None


async def test_a_single_line_value_still_refuses_a_line_feed(tmp_path):
    store = host_store(tmp_path)
    with pytest.raises(SecretRefused):
        await store.replace({"KNOWN_HOSTS:m1": "m1 ssh-ed25519 AAAA\nevil ssh-ed25519 BBBB"})
    with pytest.raises(SecretRefused):
        await store.set("GITHUB_TOKEN", "ghp_one\nghp_two")


async def test_replace_writes_a_host_key_and_its_known_hosts_entry_together_or_neither(tmp_path):
    store = host_store(tmp_path)
    with pytest.raises(SecretRefused):
        await store.replace({"SSH_KEY:m1": PEM, "KNOWN_HOSTS:m1": "bad\x00line"})
    assert store.stored(KEYS) == [] and store.stored(KNOWN) == []

    changed = await store.replace({"SSH_KEY:m1": PEM, "KNOWN_HOSTS:m1": "m1 ssh-ed25519 AAAA"})
    assert changed == {"SSH_KEY:m1", "KNOWN_HOSTS:m1"}
    assert await store.replace({"SSH_KEY:m1": None, "KNOWN_HOSTS:m1": None}) == {
        "SSH_KEY:m1",
        "KNOWN_HOSTS:m1",
    }
    assert store.get("SSH_KEY:m1") is None
    assert json.loads((tmp_path / "data" / "secrets.json").read_text())["values"] == {}


async def test_a_family_value_is_never_in_the_list_the_environ_or_read_from_the_env(tmp_path):
    store = host_store(tmp_path, env={"SSH_KEY:m1": "from the env"})
    await store.replace({"SSH_KEY:m1": PEM})

    assert store.get("SSH_KEY:m1").get_secret_value() == PEM  # the env never decides it
    assert all(not entry.name.startswith("SSH_KEY") for entry in store.names())
    assert store.environ().get("SSH_KEY:m1") == "from the env"  # the process env, not the file

    no_env = host_store(tmp_path)
    assert "SSH_KEY:m1" not in set(no_env.environ())
    assert no_env.environ().get("SSH_KEY:m1") is None


async def test_a_family_value_is_set_only_with_replace_and_a_bad_id_is_unknown(tmp_path):
    store = host_store(tmp_path)
    with pytest.raises(UnknownSecret):
        await store.set("SSH_KEY:m1", PEM)
    with pytest.raises(UnknownSecret):
        await store.clear("SSH_KEY:m1")
    for name in ("SSH_KEY:", "SSH_KEY:M1", "SSH_KEY:a/b", "SSH_KEY:-x", "GITHUB_TOKEN"):
        with pytest.raises(UnknownSecret):
            await store.replace({name: "x"})


async def test_family_values_in_the_file_are_loaded_and_a_bad_name_is_left_out(tmp_path, caplog):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "values": {"SSH_KEY:m1": PEM, "SSH_KEY:../x": PEM}}))
    path.chmod(0o600)

    store = host_store(tmp_path)
    assert store.stored(KEYS) == ["SSH_KEY:m1"]
    assert "left out" in caplog.text and "b3Blbn" not in caplog.text


async def test_a_change_to_a_family_value_tells_the_listeners_its_name_only(tmp_path, caplog):
    store = host_store(tmp_path)
    heard: list[frozenset[str]] = []

    async def listener(changed: frozenset[str]) -> None:
        heard.append(changed)

    store.subscribe(listener)
    with caplog.at_level("DEBUG"):
        await store.replace({"SSH_KEY:m1": PEM})
    assert heard == [frozenset({"SSH_KEY:m1"})]
    assert "b3Blbn" not in caplog.text and "SSH_KEY:m1 set" in caplog.text
