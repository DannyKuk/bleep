import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from bleep.graph.deps import CORE_ACTIONS
from bleep.guard.plan_check import check_plan, host_urls, protected_names
from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.registry import Registry
from bleep.incidents.settings_store import SettingsRefused, SettingsStore
from bleep.plugins.registry import PluginError, PluginRegistry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.graph import PlanStep, Proposal
from bleep.schemas.plugins import PluginUpdate
from bleep.schemas.settings import ModelSettings, SettingsUpdate, Thresholds
from bleep.store.db import Database
from bleep.watcher.detector import Detector
from fakes import COOLIFY_TOKEN, FakeCoolifyPlugin, FakeEngine, socket_host

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
    await store.update(url_override([DB_URL]))
    assert [u.url for u in registry.service(SHOP).urls] == [DB_URL]  # the URL checks read this
    assert set(host_urls(registry, "a")) == {DB_URL}  # the plan check and Verify read this
    assert [u.url for u in store.host_views()[0].services["compose:shop"].urls] == [DB_URL]
    # The service's rules come from the hosts file still: only the URLs were overridden.
    assert registry.service(SHOP).rules == ["the shop db is shared"]


async def test_clearing_an_override_brings_the_hosts_file_value_back(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    await store.update(url_override([DB_URL]))
    await store.update(SettingsUpdate.model_validate({"hosts": {"a": {}}}))
    assert [u.url for u in registry.service(SHOP).urls] == [FILE_URL]
    assert store.overrides().hosts == {}


async def test_overrides_survive_a_restart(db):
    first, _ = make_store(db, hosts_file_registry())
    await first.update(url_override([DB_URL]))
    await first.update(SettingsUpdate.model_validate({"budgets": {"daily_cost_usd": 0.25}}))
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
    await store.update(SettingsUpdate.model_validate({"hosts": {"a": {"rules": ["db rule"]}}}))
    host = registry.hosts()[0]
    assert host.config.rules == ["db rule"]  # what Gather shows the agent
    assert host.config.kind == "socket" and host.config.endpoint is not None  # connection kept


async def test_the_dashboard_can_add_protected_containers_but_never_unprotect_one(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    await registry.start()
    services = {"compose:shop": {"protected_containers": ["shop-web-1"]}}
    await store.update(SettingsUpdate.model_validate({"hosts": {"a": {"services": services}}}))
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
    await store.update(SettingsUpdate.model_validate({"hosts": {"a": {"services": services}}}))
    assert protected_names(registry.hosts()[0]) == {"shop-db-1"}


async def test_a_host_not_in_the_hosts_file_is_refused_and_nothing_changes(db):
    registry = hosts_file_registry()
    store, _ = make_store(db, registry)
    update = SettingsUpdate.model_validate(
        {"budgets": {"verify_loops": 1}, "hosts": {"ghost": {"rules": ["x"]}}}
    )
    with pytest.raises(SettingsRefused, match="ghost"):
        await store.update(update)
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
        await store.update(update)
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
    await store.update(SettingsUpdate.model_validate({"models": {"cheap_model": "openai/m2"}}))
    models = store.models()
    assert (models.model, models.cheap_model) == ("openai/m1", "openai/m2")
    assert models.openrouter_api_key.get_secret_value() == "sk-or-env-key"
    assert "sk-or-env-key" not in store.model_choice().model_dump_json()


async def test_thresholds_reach_a_running_detector_at_once(db):
    store, _ = make_store(db, hosts_file_registry())
    detector = Detector(store.thresholds, lambda: T0)
    await store.update(SettingsUpdate.model_validate({"thresholds": {"disk_percent": 75}}))
    assert detector.t.disk_percent == 75


async def test_plugin_actions_join_the_enabled_set_only_while_switched_on(db):
    store, _ = make_store(db, hosts_file_registry())
    assert store.enabled_actions() == set(CORE_ACTIONS)
    await store.set_plugin(
        "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    assert "coolify.redeploy" in store.enabled_actions()
    await store.set_plugin("coolify", PluginUpdate(enabled=False))
    assert store.enabled_actions() == set(CORE_ACTIONS)


async def test_a_plugin_toggle_and_its_settings_survive_a_restart(db):
    store, _ = make_store(db, hosts_file_registry())
    await store.set_plugin(
        "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    again, plugins = make_store(db, hosts_file_registry())
    await again.load()
    assert plugins.is_enabled("coolify")
    assert plugins.settings("coolify").url == "https://c.example.test"


async def test_a_plugin_whose_secret_is_gone_at_boot_starts_off_and_bleep_starts(db):
    store, _ = make_store(db, hosts_file_registry())
    await store.set_plugin(
        "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
    )
    again, plugins = make_store(db, hosts_file_registry(), env={})
    await again.load()
    assert not plugins.is_enabled("coolify")
    assert plugins.settings("coolify").url == "https://c.example.test"


async def test_a_plugin_change_that_cannot_be_saved_leaves_the_plugin_as_it_was(db, monkeypatch):
    store, plugins = make_store(db, hosts_file_registry())

    async def broken(sections):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", broken)
    with pytest.raises(OSError):
        await store.set_plugin(
            "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
        )
    assert not plugins.is_enabled("coolify")
    assert store.enabled_actions() == set(CORE_ACTIONS)


async def test_switching_on_without_the_secret_is_refused(db):
    store, plugins = make_store(db, hosts_file_registry(), env={})
    with pytest.raises(PluginError, match="COOLIFY_TOKEN"):
        await store.set_plugin(
            "coolify", PluginUpdate(enabled=True, settings={"url": "https://c.example.test"})
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
    await store.set_plugin("coolify", PluginUpdate(settings={"url": "https://c2.test"}))
    assert not plugins.is_enabled("coolify")
    again, plugins_after = make_store(db, hosts_file_registry())  # the secret is back
    await again.load()
    assert plugins_after.is_enabled("coolify")
    assert plugins_after.settings("coolify").url == "https://c2.test"
