import asyncio
import json
import logging
import os
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.plan_check import check_plan, check_target, host_urls, protected_names
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.settings_store import SettingsRefused, SettingsStale, SettingsStore
from bleep.plugins.registry import PluginError, PluginRegistry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import PlanStep, Proposal
from bleep.schemas.plugins import PluginUpdate
from bleep.schemas.settings import ModelSettings, SettingsUpdate, Thresholds
from bleep.store.db import Database
from bleep.watcher.detector import Detector
from fakes import COOLIFY_TOKEN, FakeCoolifyPlugin, FakeEngine, socket_host, ssh_host

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
FILE_URL = "https://shop.example.test/health"
DB_URL = "https://shop.example.test/ready"
SHOP = "a/compose:shop"


def web(name: str = "shop-web-1") -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}",
        name=name,
        image="shop:1",
        state="running",
        labels={"com.docker.compose.project": "shop"},
    )


def hosts_file_registry() -> Registry:
    host = socket_host(
        id="a",
        rules=["file rule"],
        services={
            "compose:shop": ServiceOverrides(
                urls=[UrlCheckConfig(url=FILE_URL)],
                rules=["the shop db is shared"],
                protected_containers=["shop-db-1"],
            )
        },
    )

    async def connect(_host):
        return None, FakeEngine("a", [web(), web("shop-db-1")])

    return Registry([host], connect, clock=lambda: T0)


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(tmp_path / "bleep.db")
    await database.migrate()
    return database


def make_store(db: Database, registry: Registry, env: dict[str, str] | None = None):
    plugins = PluginRegistry(
        [FakeCoolifyPlugin()],
        SecretStr("s"),
        env={"COOLIFY_TOKEN": COOLIFY_TOKEN} if env is None else env,
    )
    models = ModelSettings(openrouter_api_key=SecretStr("sk-or-env-key"), model="openai/m1")
    return SettingsStore(db, registry, plugins, models=models, clock=lambda: T0), plugins


async def save(store: SettingsStore, update: SettingsUpdate | dict) -> None:
    """A change built from the overrides as they are now."""
    if isinstance(update, dict):
        update = SettingsUpdate.model_validate(update)
    await store.update(update, version=store.version())


async def switch(store: SettingsStore, name: str, update: PluginUpdate) -> None:
    """A plugin change built from the plugin as it is now."""
    await store.set_plugin(name, update, version=store.plugin_version(name))


def url_override(urls: list[str]) -> SettingsUpdate:
    services = {"compose:shop": {"urls": [{"url": u} for u in urls]}}
    return SettingsUpdate.model_validate({"hosts": {"a": {"services": services}}})


async def test_without_overrides_the_hosts_file_and_the_defaults_hold(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await store.load()
    await registry.start()
    assert store.budgets().investigate_tool_calls == 8
    assert store.thresholds() == Thresholds()
    assert [u.url for u in registry.service(SHOP).urls] == [FILE_URL]
    assert store.host_views()[0].rules == ["file rule"]


async def test_a_database_url_wins_over_the_hosts_file_everywhere_it_is_read(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await save(store, url_override([DB_URL]))
    assert [u.url for u in registry.service(SHOP).urls] == [DB_URL]  # the URL checks read this
    assert set(host_urls(registry, "a")) == {DB_URL}  # the plan check and Verify read this
    assert [u.url for u in store.host_views()[0].services["compose:shop"].urls] == [DB_URL]
    # The service's rules come from the hosts file still: only the URLs were overridden.
    assert registry.service(SHOP).rules == ["the shop db is shared"]


async def test_clearing_an_override_brings_the_hosts_file_value_back(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await save(store, url_override([DB_URL]))
    await save(store, {"hosts": {"a": {}}})
    assert [u.url for u in registry.service(SHOP).urls] == [FILE_URL]
    assert store.overrides().hosts == {}


async def test_overrides_survive_a_restart(db):
    first, _ = make_store(db, hosts_file_registry())
    await save(first, url_override([DB_URL]))
    await save(first, {"budgets": {"daily_cost_usd": 0.25}})
    registry = hosts_file_registry()
    second, _ = make_store(db, registry)
    await second.load()
    await registry.start()
    assert [u.url for u in registry.service(SHOP).urls] == [DB_URL]
    assert second.budgets().daily_cost_usd == 0.25
    assert second.budgets().investigate_tool_calls == 8  # the default under the override


async def test_host_rules_override_and_the_service_overrides_stay(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await save(store, {"hosts": {"a": {"rules": ["db rule"]}}})
    host = registry.hosts()[0]
    assert host.config.rules == ["db rule"]  # what Gather shows the agent
    assert host.config.kind == "socket" and host.config.endpoint is not None  # connection kept


async def test_may_sleep_comes_from_the_hosts_file_and_the_dashboard_overrides_it(db):
    host = socket_host(id="a", may_sleep=True)

    async def connect(_host):
        return None, FakeEngine("a", [web()])

    registry = Registry([host], connect, clock=lambda: T0)
    store, _ = make_store(db, registry)
    await registry.start()
    assert registry.hosts()[0].config.may_sleep is True
    assert store.host_views()[0].may_sleep is True

    await save(store, {"hosts": {"a": {"may_sleep": False}}})
    assert registry.hosts()[0].config.may_sleep is False
    assert store.host_views()[0].may_sleep is False

    await save(store, {"hosts": {"a": {}}})  # cleared: the hosts file's value holds again
    assert registry.hosts()[0].config.may_sleep is True


async def test_dashboard_exits_replace_the_hosts_files_like_urls(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await save(store, {"hosts": {"a": {"services": {"compose:shop": {"exits": ["fetch"]}}}}})
    assert registry.service(SHOP).exits == ["fetch"]
    assert store.host_views()[0].services["compose:shop"].exits == ["fetch"]


async def test_this_is_expected_adds_to_exits_under_the_key_the_hosts_file_used(db):
    host = socket_host(
        id="a", services={"shop": ServiceOverrides(urls=[UrlCheckConfig(url=FILE_URL)])}
    )

    async def connect(_host):
        return None, FakeEngine("a", [web()])

    registry = Registry([host], connect, clock=lambda: T0)
    store, _ = make_store(db, registry)
    await registry.start()
    version = store.version()
    await store.expect_exits(registry.service(SHOP), ["fetch"])
    await store.expect_exits(registry.service(SHOP), ["fetch", "backup"])

    shop = registry.service(SHOP)
    assert shop.exits == ["fetch", "backup"]
    assert [u.url for u in shop.urls] == [FILE_URL]  # the file's name-keyed entry still holds
    assert store.overrides().hosts["a"].services["shop"].exits == ["fetch", "backup"]
    assert store.version() != version  # a page that saves from the old version gets its 409


async def test_this_is_expected_marks_a_host_as_one_that_may_sleep(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await store.expect_sleep("a")
    assert registry.hosts()[0].config.may_sleep is True
    assert registry.hosts()[0].config.rules == ["file rule"]


async def test_the_dashboard_can_add_protected_containers_but_never_unprotect_one(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    services = {"compose:shop": {"protected_containers": ["shop-web-1"]}}
    await save(store, {"hosts": {"a": {"services": services}}})
    assert protected_names(registry.hosts()[0]) == {"shop-db-1", "shop-web-1"}
    step = PlanStep(action="docker.restart", host_id="a", target="shop-web-1")
    proposal = Proposal(kind="act", steps=[step], reason="r")
    rules = {
        v.rule
        for v in check_plan(
            proposal, registry, set(CORE_ACTIONS), "a", incident_containers=["shop-web-1"]
        )
    }
    assert "protected" in rules
    services = {"compose:shop": {"protected_containers": []}}
    await save(store, {"hosts": {"a": {"services": services}}})
    assert protected_names(registry.hosts()[0]) == {"shop-db-1"}


async def test_a_coolify_application_can_be_protected_by_the_name_as_written(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    services = {"compose:shop": {"protected_containers": ["Shop API", "name:main", " Café Shop "]}}
    await save(store, {"hosts": {"a": {"services": services}}})
    expected = {"shop-db-1", "Shop API", "name:main", "Café Shop"}
    assert protected_names(registry.hosts()[0]) == expected


@pytest.mark.parametrize("name", ["shop\nweb", "shop\tweb", "shop\x1b[2Jweb", "shop\u2028web", " "])
async def test_a_protected_entry_with_a_control_character_or_only_spaces_is_refused(db, name):
    store, _ = make_store(db, hosts_file_registry())
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate(
            {"hosts": {"a": {"services": {"compose:shop": {"protected_containers": [name]}}}}}
        )
    assert store.overrides().hosts == {}


async def test_a_host_not_in_the_hosts_file_is_refused_and_nothing_changes(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    update = SettingsUpdate.model_validate(
        {"budgets": {"verify_loops": 1}, "hosts": {"ghost": {"rules": ["x"]}}}
    )
    with pytest.raises(SettingsRefused, match="ghost"):
        await save(store, update)
    assert store.budgets().verify_loops == 2
    reloaded, _ = make_store(db, hosts_file_registry())
    await reloaded.load()
    assert reloaded.budgets().verify_loops == 2


async def test_a_secret_in_a_rule_is_refused(db):
    store, _ = make_store(db, hosts_file_registry())
    update = SettingsUpdate.model_validate(
        {"hosts": {"a": {"rules": ["log in with password=hunter2hunter2"]}}}
    )
    with pytest.raises(SettingsRefused) as refusal:
        await save(store, update)
    assert "hunter2" not in str(refusal.value)
    assert "hosts.a.rules[0]" in str(refusal.value)


def test_bounds_keep_a_budget_from_switching_a_guard_off():
    for bad in ({"investigate_tool_calls": 0}, {"daily_cost_usd": -1}, {"verify_loops": 99}):
        with pytest.raises(ValidationError):
            SettingsUpdate.model_validate({"budgets": bad})
    with pytest.raises(ValidationError):
        SettingsUpdate.model_validate(
            {"hosts": {"a": {"services": {"s": {"urls": [{"url": "ftp://x"}]}}}}}
        )


async def test_the_model_choice_changes_and_the_env_key_stays(db):
    store, _ = make_store(db, hosts_file_registry())
    await save(store, {"models": {"cheap_model": "openai/m2"}})
    models = store.models()
    assert (models.model, models.cheap_model) == ("openai/m1", "openai/m2")
    assert models.openrouter_api_key.get_secret_value() == "sk-or-env-key"
    assert "sk-or-env-key" not in store.model_choice().model_dump_json()


async def test_thresholds_reach_a_running_detector_at_once(db):
    store, _ = make_store(db, hosts_file_registry())
    detector = Detector(store.thresholds, lambda: T0)
    await save(store, {"thresholds": {"disk_percent": 75}})
    assert detector.t.disk_percent == 75


async def test_plugin_actions_join_the_enabled_set_only_while_switched_on(db):
    store, _ = make_store(db, hosts_file_registry())
    assert store.enabled_actions() == set(CORE_ACTIONS)
    await switch(
        store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    assert "coolify.redeploy" in store.enabled_actions()
    await switch(store, "coolify", PluginUpdate(enabled=False))
    assert store.enabled_actions() == set(CORE_ACTIONS)


async def test_a_plugin_toggle_and_its_settings_survive_a_restart(db):
    store, _ = make_store(db, hosts_file_registry())
    await switch(
        store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    again, plugins = make_store(db, hosts_file_registry())
    await again.load()
    assert plugins.is_enabled("coolify")
    assert plugins.settings("coolify").url == "https://c.example.test"


async def test_a_plugin_whose_secret_is_gone_at_boot_starts_off_and_bleep_starts(db):
    store, _ = make_store(db, hosts_file_registry())
    await switch(
        store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    again, plugins = make_store(db, hosts_file_registry(), env={})
    await again.load()
    assert not plugins.is_enabled("coolify")
    assert plugins.settings("coolify").url == "https://c.example.test"


async def test_a_plugin_waiting_for_its_secret_shows_switched_on_and_the_log_says_so(db, caplog):
    await _store_raw(db, "plugin:coolify", {"enabled": True, "settings": {"url": "https://c.test"}})
    store, plugins = make_store(db, hosts_file_registry(), env={})
    with caplog.at_level(logging.WARNING):
        await store.load()
    view = await store.plugin_view("coolify")
    assert (view.enabled, view.switched_on) == (False, True)
    assert [s.env for s in view.secrets if s.required and not s.set] == ["COOLIFY_TOKEN"]
    assert "left out" not in caplog.text
    assert "plugin coolify stays switched on but does not run" in caplog.text
    assert "COOLIFY_TOKEN" in caplog.text


async def test_a_plugin_waiting_for_its_secret_can_be_switched_off_for_the_next_boot(db):
    await _store_raw(db, "plugin:coolify", {"enabled": True, "settings": {"url": "https://c.test"}})
    store, _ = make_store(db, hosts_file_registry(), env={})
    await store.load()
    await switch(store, "coolify", PluginUpdate(enabled=False))
    view = await store.plugin_view("coolify")
    assert (view.enabled, view.switched_on) == (False, False)

    again, plugins = make_store(db, hosts_file_registry())  # the secret is set now
    await again.load()
    assert not plugins.is_enabled("coolify")
    assert plugins.settings("coolify").url == "https://c.test"


async def test_a_plugin_change_that_cannot_be_saved_leaves_the_plugin_as_it_was(db, monkeypatch):
    store, plugins = make_store(db, hosts_file_registry())

    async def broken(sections):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", broken)
    with pytest.raises(OSError):
        await switch(
            store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
        )
    assert not plugins.is_enabled("coolify")
    assert store.enabled_actions() == set(CORE_ACTIONS)


async def test_a_view_read_during_a_plugin_save_that_then_fails_shows_nothing_unstored(
    db, monkeypatch
):
    store, plugins = make_store(db, hosts_file_registry())
    writing, release = asyncio.Event(), asyncio.Event()

    async def failing_write(sections):
        writing.set()
        await release.wait()
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", failing_write)
    update = PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    saving = asyncio.create_task(switch(store, "coolify", update))
    await writing.wait()
    reading = asyncio.create_task(store.plugin_view("coolify"))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(OSError):
        await saving
    view = await reading
    assert not view.enabled
    assert view.settings.get("url") != "https://c.example.test"
    assert view.version == store.plugin_version("coolify")


async def test_switching_on_without_the_secret_is_refused(db):
    store, plugins = make_store(db, hosts_file_registry(), env={})
    with pytest.raises(PluginError, match="COOLIFY_TOKEN"):
        await switch(
            store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
        )
    assert not plugins.is_enabled("coolify")


async def _store_raw(db: Database, key: str, value: object) -> None:
    conn = await db.connect()
    try:
        await conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), T0.isoformat()),
        )
        await conn.commit()
    finally:
        await conn.close()


async def test_a_stored_row_that_no_longer_fits_is_left_out_and_the_rest_loads(db):
    await _store_raw(db, "host:gone", {"rules": ["x"]})
    await _store_raw(db, "budgets", {"verify_loops": 1})
    await _store_raw(db, "thresholds", {"no_such_field": 1})
    await _store_raw(db, "plugin:retired", {"enabled": True, "settings": {}})
    await _store_raw(db, "models", {"model": "has spaces"})
    store, _ = make_store(db, hosts_file_registry())
    await store.load()
    assert store.budgets().verify_loops == 1
    assert store.thresholds() == Thresholds()
    assert store.models().model == "openai/m1"
    assert "gone" not in (store.overrides().hosts or {})


async def test_a_stored_row_holding_a_secret_is_never_loaded(db):
    await _store_raw(db, "host:a", {"rules": ["api_key=sk-live-0123456789abcdef"]})
    await _store_raw(db, "plugin:coolify", {"enabled": False, "settings": {"token": "x"}})
    registry = hosts_file_registry()
    store, plugins = make_store(db, registry)
    await store.load()
    assert registry.hosts()[0].config.rules == ["file rule"]
    assert store.overrides().hosts == {}
    assert plugins.snapshot("coolify") == (None, None)  # the plugin row was not applied


async def test_a_stored_plugin_whose_code_now_fails_to_build_does_not_stop_bleep(db):
    class Exploding(FakeCoolifyPlugin):
        def tools(self, ctx):
            raise RuntimeError("cannot build")

    await _store_raw(db, "plugin:coolify", {"enabled": True, "settings": {"url": "https://c.test"}})
    await _store_raw(db, "budgets", {"verify_loops": 1})
    plugins = PluginRegistry([Exploding()], SecretStr("s"), env={"COOLIFY_TOKEN": COOLIFY_TOKEN})
    store = SettingsStore(db, hosts_file_registry(), plugins, clock=lambda: T0)
    await store.load()
    assert not plugins.is_enabled("coolify")
    assert store.budgets().verify_loops == 1


async def test_a_settings_change_alone_keeps_the_stored_toggle_of_a_plugin_off_for_its_secret(db):
    await _store_raw(db, "plugin:coolify", {"enabled": True, "settings": {"url": "https://c.test"}})
    store, plugins = make_store(db, hosts_file_registry(), env={})
    await store.load()
    await switch(store, "coolify", PluginUpdate(settings={"url": "https://c2.test"}))
    assert not plugins.is_enabled("coolify")
    again, plugins_after = make_store(db, hosts_file_registry())  # the secret is back
    await again.load()
    assert plugins_after.is_enabled("coolify")
    assert plugins_after.settings("coolify").url == "https://c2.test"


async def test_host_views_show_the_connection_from_the_hosts_file_and_never_a_key(db, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n")
    ssh = ssh_host(id="m1", key_file=key)
    gone = ssh_host(id="m2", key_file=tmp_path / "missing")
    socket = socket_host(id="a", endpoint="tcp://socket-proxy:2375")

    async def connect(_host):
        return None, FakeEngine("a", [])

    store, _ = make_store(db, Registry([ssh, gone, socket], connect, clock=lambda: T0))
    m1, m2, a = (view.connection for view in store.host_views())
    assert (m1.kind, m1.address, m1.user, m1.port) == ("ssh", "m1.local", "danny", 2222)
    assert m1.docker_socket == "/Users/danny/.docker/run/docker.sock"
    assert m1.key_file is not None and m1.key_file.file == str(key) and m1.key_file.set
    assert m2.key_file is not None and not m2.key_file.set
    assert a.kind == "socket" and a.endpoint == "tcp://socket-proxy:2375"
    assert a.address is None and a.key_file is None and a.port is None
    assert "not-a-real-key" not in str([v.model_dump() for v in store.host_views()])


async def test_host_views_say_which_protected_containers_the_hosts_file_fixes(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    services = {"compose:shop": {"protected_containers": ["shop-web-1", "shop-db-1"]}}
    await save(store, {"hosts": {"a": {"services": services}}})
    shop = store.host_views()[0].services["compose:shop"]
    assert shop.protected_containers == ["shop-db-1", "shop-web-1"]
    assert shop.hosts_file_protected_containers == ["shop-db-1"]


async def test_a_change_built_from_a_stale_version_is_refused_and_nothing_changes(db):
    store, _ = make_store(db, hosts_file_registry())
    read = store.version()
    protect = {"compose:shop": {"protected_containers": ["shop-web-1"]}}
    await save(store, {"hosts": {"a": {"services": protect}}})
    after = store.version()
    assert after != read  # protecting a container is a new version

    stale = SettingsUpdate.model_validate({"hosts": {"a": {"rules": ["stale rule"]}}})
    with pytest.raises(SettingsStale) as refused:
        await store.update(stale, version=read)
    assert refused.value.current == after
    assert store.version() == after
    assert "shop-web-1" in store.host_views()[0].services["compose:shop"].protected_containers
    assert store.host_views()[0].rules == ["file rule"]
    again, _ = make_store(db, hosts_file_registry())
    await again.load()
    assert again.overrides() == store.overrides()  # nothing written either


async def test_of_two_writers_from_one_version_only_the_first_is_applied(db):
    store, _ = make_store(db, hosts_file_registry())
    read = store.version()
    first = SettingsUpdate.model_validate({"thresholds": {"disk_percent": 80}})
    second = SettingsUpdate.model_validate({"thresholds": {"memory_polls": 9}})
    results = await asyncio.gather(
        store.update(first, version=read),
        store.update(second, version=read),
        return_exceptions=True,
    )
    assert results[0] is None and isinstance(results[1], SettingsStale)
    assert store.thresholds().disk_percent == 80
    assert store.thresholds().memory_polls == Thresholds().memory_polls


async def test_the_version_holds_across_a_restart_and_follows_the_content(db):
    first, _ = make_store(db, hosts_file_registry())
    empty = first.version()
    await save(first, {"budgets": {"daily_cost_usd": 0.25}})
    second, _ = make_store(db, hosts_file_registry())
    await second.load()
    assert second.version() == first.version() != empty
    await save(second, {"budgets": {}})
    assert second.version() == empty


async def test_a_plugin_change_leaves_the_version_alone(db):
    store, _ = make_store(db, hosts_file_registry())
    before = store.version()
    await switch(store, "coolify", PluginUpdate(settings={"url": "https://c.test"}))
    assert store.version() == before


async def test_a_settings_change_leaves_a_plugin_version_alone(db):
    store, _ = make_store(db, hosts_file_registry())
    before = store.plugin_version("coolify")
    await save(store, {"thresholds": {"disk_percent": 80}})
    assert store.plugin_version("coolify") == before


async def test_a_plugin_change_from_a_stale_version_is_refused_and_nothing_changes(db):
    """A tab read before a setting was changed elsewhere would put the old one back."""
    store, plugins = make_store(db, hosts_file_registry())
    read = store.plugin_version("coolify")
    await switch(store, "coolify", PluginUpdate(settings={"url": "https://new.test"}))
    after = store.plugin_version("coolify")
    assert after != read

    with pytest.raises(SettingsStale) as refused:
        await store.set_plugin(
            "coolify", PluginUpdate(settings={"url": "https://old.test"}), version=read
        )
    assert refused.value.current == after
    assert plugins.settings("coolify").url == "https://new.test"
    again, plugins_after = make_store(db, hosts_file_registry())
    await again.load()
    assert plugins_after.settings("coolify").url == "https://new.test"


async def test_of_two_plugin_writers_from_one_version_only_the_first_is_applied(db):
    store, plugins = make_store(db, hosts_file_registry())
    read = store.plugin_version("coolify")
    results = await asyncio.gather(
        store.set_plugin("coolify", PluginUpdate(settings={"url": "https://a.test"}), version=read),
        store.set_plugin("coolify", PluginUpdate(settings={"url": "https://b.test"}), version=read),
        return_exceptions=True,
    )
    assert results[0] is None and isinstance(results[1], SettingsStale)
    assert plugins.settings("coolify").url == "https://a.test"


async def test_a_plugin_version_holds_across_a_restart(db):
    store, _ = make_store(db, hosts_file_registry())
    await switch(store, "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.test"}))
    again, _ = make_store(db, hosts_file_registry())
    await again.load()
    assert again.plugin_version("coolify") == store.plugin_version("coolify")


async def test_a_connection_never_shows_the_userinfo_of_an_endpoint_or_address(db):
    hosts = [
        socket_host(id="a", endpoint="tcp://bleep:hunter2@socket-proxy:2375"),
        socket_host(id="b", endpoint="tcp://p/a@ss@proxy:2375"),
        ssh_host(id="m1", address="danny:hunter2@m1.local"),
    ]

    async def connect(_host):
        return None, FakeEngine("a", [])

    store, _ = make_store(db, Registry(hosts, connect, clock=lambda: T0))
    a, b, m1 = (view.connection for view in store.host_views())
    assert a.endpoint == "tcp://<redacted>@socket-proxy:2375"
    assert b.endpoint == "tcp://<redacted>@proxy:2375"
    assert m1.address == "<redacted>@m1.local"
    shown = str([v.model_dump() for v in store.host_views()])
    assert "hunter2" not in shown and "bleep:" not in shown and "p/a" not in shown


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
async def test_a_key_file_bleep_cannot_read_is_not_set(db, tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("not-a-real-key\n")
    key.chmod(0o000)
    folder = tmp_path / "a-folder"
    folder.mkdir()

    async def connect(_host):
        return None, FakeEngine("a", [])

    hosts = [ssh_host(id="m1", key_file=key), ssh_host(id="m2", key_file=folder)]
    store, _ = make_store(db, Registry(hosts, connect, clock=lambda: T0))
    try:
        m1, m2 = (view.connection for view in store.host_views())
        assert m1.key_file is not None and not m1.key_file.set
        assert m2.key_file is not None and not m2.key_file.set
    finally:
        key.chmod(0o600)


def name_keyed_registry(services: dict[str, ServiceOverrides]) -> Registry:
    host = socket_host(id="a", services=services)

    async def connect(_host):
        return None, FakeEngine("a", [web(), web("shop-db-1")])

    return Registry([host], connect, clock=lambda: T0)


def refusals(registry: Registry, target: str) -> set[str]:
    """What the plan check and, right before its engine call, the act tool refuse."""
    step = PlanStep(action="docker.restart", host_id="a", target=target)
    proposal = Proposal(kind="act", steps=[step], reason="r")
    planned = check_plan(proposal, registry, set(CORE_ACTIONS), "a", incident_containers=[target])
    return {v.rule for v in planned} | {v.rule for v in check_target(1, step, registry)}


async def test_a_dashboard_override_under_the_key_keeps_the_hosts_files_name_keyed_protection(db):
    registry = name_keyed_registry({"shop": ServiceOverrides(protected_containers=["shop-db-1"])})
    store, _ = make_store(db, registry)
    await registry.start()
    services = {"compose:shop": {"urls": [{"url": DB_URL}], "rules": ["dashboard rule"]}}
    await save(store, {"hosts": {"a": {"services": services}}})
    shop = registry.service(SHOP)
    assert shop.protected == {"shop-db-1"}
    assert [u.url for u in shop.urls] == [DB_URL]
    assert shop.rules == ["dashboard rule"]
    assert "protected" in refusals(registry, "shop-db-1")
    assert "protected" not in refusals(registry, "shop-web-1")
    view = store.host_views()[0].services["compose:shop"]
    assert view.hosts_file_protected_containers == ["shop-db-1"]


async def test_a_dashboard_override_under_the_name_keeps_the_hosts_files_key_protection(db):
    registry = name_keyed_registry(
        {"compose:shop": ServiceOverrides(protected_containers=["shop-db-1"])}
    )
    store, _ = make_store(db, registry)
    await registry.start()
    services = {"shop": {"urls": [{"url": DB_URL}], "protected_containers": ["shop-web-1"]}}
    await save(store, {"hosts": {"a": {"services": services}}})
    assert registry.service(SHOP).protected == {"shop-db-1", "shop-web-1"}
    assert "protected" in refusals(registry, "shop-db-1")
    view = store.host_views()[0].services["shop"]
    assert view.hosts_file_protected_containers == ["shop-db-1"]


# --- exits: validated when written, salvaged when read ---


async def test_a_bad_stored_exits_entry_is_left_out_and_dashboard_protection_survives_a_restart(
    db,
):
    services = {
        "compose:shop": {"protected_containers": ["shop-web-1"], "exits": ["x" * 300]},
    }
    await _store_raw(db, "host:a", {"rules": ["host rule"], "services": services})
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await store.load()
    await registry.start()

    assert "protected" in refusals(registry, "shop-web-1")  # the dashboard's protection held
    assert registry.service(SHOP).exits == []
    assert registry.hosts()[0].config.rules == ["host rule"]


@pytest.mark.parametrize(
    "exits",
    [["x" * 300], ["*", "fetch"], ["-dash"], ["a b"], [f"job{n}" for n in range(101)]],
)
async def test_an_exits_list_that_is_no_list_of_names_is_refused(db, exits):
    store, _ = make_store(db, hosts_file_registry())
    with pytest.raises((SettingsRefused, ValidationError)):
        await save(store, {"hosts": {"a": {"services": {"compose:shop": {"exits": exits}}}}})


async def test_this_is_expected_never_writes_a_star_a_bad_name_or_past_the_cap(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    for entries in (["*"], ["x" * 300]):
        with pytest.raises(SettingsRefused):
            await store.expect_exits(registry.service(SHOP), entries)
    await store.expect_exits(registry.service(SHOP), [f"job{n}" for n in range(100)])
    with pytest.raises(SettingsRefused):
        await store.expect_exits(registry.service(SHOP), ["one-too-many"])
    assert len(registry.service(SHOP).exits) == 100
