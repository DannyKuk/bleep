"""The secret file: written only by the backend's user, never read back through anything but
`get`, the env wins over it, and a plugin's address is bound to its token (#135)."""

import json
import os
import stat

import pytest

from bleep.store.secrets import (
    SecretFileError,
    SecretFromEnv,
    SecretName,
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
    await store.set("COOLIFY_TOKEN", TOKEN)

    changed = await store.set("COOLIFY_URL", "https://elsewhere.example.com")

    assert changed == {"COOLIFY_URL", "COOLIFY_TOKEN"}
    assert store.get("COOLIFY_TOKEN") is None
    assert TOKEN not in (tmp_path / "data" / "secrets.json").read_text()
    assert store_at(tmp_path).get("COOLIFY_TOKEN") is None


async def test_the_same_address_again_keeps_the_token(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await store.set("COOLIFY_TOKEN", TOKEN)
    assert await store.set("COOLIFY_URL", "https://coolify.example.com ") == set()
    assert store.get("COOLIFY_TOKEN") is not None


async def test_clearing_an_address_clears_its_token(tmp_path):
    store = store_at(tmp_path)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await store.set("COOLIFY_TOKEN", TOKEN)
    assert await store.clear("COOLIFY_URL") == {"COOLIFY_URL", "COOLIFY_TOKEN"}
    assert store.get("COOLIFY_TOKEN") is None


async def test_a_token_needs_its_address_first(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(SecretRefused, match="COOLIFY_URL first"):
        await store.set("COOLIFY_TOKEN", TOKEN)


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
    await store.set("COOLIFY_TOKEN", TOKEN)
    assert view["COOLIFY_TOKEN"] == TOKEN  # live, not a copy


async def test_listeners_hear_which_names_changed_and_never_the_values(tmp_path):
    store = store_at(tmp_path)
    heard: list[set[str]] = []

    async def listen(changed: frozenset[str]) -> None:
        heard.append(set(changed))

    store.subscribe(listen)
    await store.set("COOLIFY_URL", "https://coolify.example.com")
    await store.set("COOLIFY_TOKEN", TOKEN)
    await store.set("COOLIFY_TOKEN", TOKEN)  # unchanged: nobody is told
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
    await store.set("COOLIFY_TOKEN", TOKEN)
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
