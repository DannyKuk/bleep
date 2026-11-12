"""The URL checks a Coolify application's domains suggest for its service (#348), read from the
recorded `domains.json`; no network."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from bleep.hosts.config import UrlCheckConfig
from bleep.hosts.discovery import group_services
from bleep.plugins.base import Plugin, PluginContext
from bleep.plugins.coolify.plugin import CoolifyPlugin, CoolifySettings, suggested_checks
from bleep.plugins.registry import PluginRegistry
from bleep.schemas.coolify import CoolifyApplication
from bleep.schemas.plugins import PluginHealth
from bleep.schemas.services import Service
from bleep.tools.registry import ToolSpec
from fakes import CoolifyAnswers
from graph_env import box

FIXTURES = Path(__file__).parent.parent / "fixtures" / "coolify"
BASE = "https://coolify.example.com"
SHOP = "shop0uuid0000000000000a1"
BLOG = "blog0uuid0000000000000b2"
NOTES = "note0uuid0000000000000c3"
FORUM = "forum0uuid000000000000d4"


def apps() -> list[CoolifyApplication]:
    raw = json.loads((FIXTURES / "domains.json").read_text(encoding="utf-8"))
    return [CoolifyApplication.model_validate(item) for item in raw]


def coolify_labels(uuid: str, resource: str, pr: str = "0") -> dict[str, str]:
    return {
        "coolify.managed": "true",
        "coolify.type": "application",
        "coolify.applicationUuid": uuid,
        "coolify.resourceName": resource,
        "coolify.pullRequestId": pr,
    }


def service_of(*containers, urls: list[str] = ()) -> Service:
    [service] = group_services("a", list(containers), {})
    return service.model_copy(update={"urls": [UrlCheckConfig(url=u) for u in urls]})


def urls(checks: list[UrlCheckConfig]) -> list[str]:
    return [check.url for check in checks]


def test_each_domain_is_suggested_with_the_health_check_path_without_its_port():
    shop = service_of(box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")))
    assert urls(suggested_checks(shop, apps())) == [
        "https://shop.example.com/health",
        "https://WWW.shop.example.com/health",
        "https://example.com/shop/health",
    ]
    assert all(
        c.expect_status == 200 and c.body_contains is None for c in suggested_checks(shop, apps())
    )


def test_userinfo_preview_and_non_http_domains_are_never_suggested_and_off_means_slash():
    blog = service_of(box(f"{BLOG}-074501234567", labels=coolify_labels(BLOG, "blog")))
    suggested = urls(suggested_checks(blog, apps()))
    assert suggested == ["http://blog.example.com/"]
    assert not any("basic-fake-password" in url for url in suggested)


def test_a_health_check_path_that_is_not_a_path_falls_back_to_slash():
    notes = service_of(box(f"{NOTES}-074501234567", labels=coolify_labels(NOTES, "notes")))
    assert urls(suggested_checks(notes, apps())) == [
        "https://notes.example.com/",
        "http://203.0.113.7/",  # all digits, an address: not a preview's `{{pr_id}}.` label
    ]


def test_checks_the_service_has_are_not_suggested_again():
    shop = service_of(
        box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")),
        urls=["https://Shop.Example.com/health/", "https://example.com/shop/health"],
    )
    assert urls(suggested_checks(shop, apps())) == ["https://WWW.shop.example.com/health"]


def test_a_preview_deployment_is_offered_nothing_though_it_carries_its_apps_uuid():
    preview = service_of(box(f"{SHOP}-pr-12-0745", labels=coolify_labels(SHOP, "shop", pr="12")))
    assert suggested_checks(preview, apps()) == []


def test_an_app_from_coolify_4_1_is_found_by_its_container_name():
    labels = {"coolify.managed": "true", "coolify.type": "application", "coolify.name": "x"}
    shop = service_of(box(f"{SHOP}-074501234567", labels=labels))
    assert shop.source == "coolify"
    assert "https://shop.example.com/health" in urls(suggested_checks(shop, apps()))


@pytest.mark.parametrize(
    "container",
    [
        box("shop-web-1", "shop"),  # a Compose project, not Coolify's
        box(f"{FORUM}-0745", labels=coolify_labels(FORUM, "forum")),  # a Compose app: no fqdn
        box("other-0745", labels=coolify_labels("gone0uuid000000000000099", "gone")),
    ],
)
def test_a_service_without_a_coolify_app_with_domains_is_offered_nothing(container):
    assert suggested_checks(service_of(container), apps()) == []


def context() -> PluginContext:
    return PluginContext(
        settings=CoolifySettings(),
        secrets={"COOLIFY_URL": SecretStr(BASE), "COOLIFY_TOKEN": SecretStr("cool-token")},
        grant_secret=SecretStr("test-secret"),
    )


def answering(status: int, body: object) -> CoolifyPlugin:
    return CoolifyPlugin(transport=CoolifyAnswers(status, body))


async def test_the_plugin_reads_the_applications_and_suggests_their_domains():
    domains = json.loads((FIXTURES / "domains.json").read_text(encoding="utf-8"))
    plugin = answering(200, domains)
    shop = service_of(box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")))
    assert len(await plugin.suggested_checks(context(), shop)) == 3


@pytest.mark.parametrize("status", [401, 500, 404])
async def test_a_coolify_failure_suggests_nothing(status):
    plugin = answering(status, {"message": "nope"})
    shop = service_of(box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")))
    assert await plugin.suggested_checks(context(), shop) == []


class Broken(Plugin):
    name = "broken"
    title = "Broken"
    settings_model = CoolifySettings

    def tools(self, ctx: PluginContext) -> list[ToolSpec]:
        return []

    async def suggested_checks(self, ctx, service) -> list[UrlCheckConfig]:
        raise RuntimeError("echoes a secret")

    async def health(self, ctx: PluginContext) -> PluginHealth:
        return PluginHealth(ok=True, detail="ok")


async def test_the_registry_asks_only_switched_on_plugins_and_a_broken_one_suggests_none():
    domains = json.loads((FIXTURES / "domains.json").read_text(encoding="utf-8"))
    coolify = answering(200, domains)
    env = {"COOLIFY_URL": BASE, "COOLIFY_TOKEN": "cool-token"}
    registry = PluginRegistry([coolify, Broken()], SecretStr("test-secret"), env=env)
    shop = service_of(box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")))
    assert await registry.suggested_checks(shop) == []
    registry.enable("broken", True)
    assert await registry.suggested_checks(shop) == []
    registry.enable("coolify", True)
    assert len(await registry.suggested_checks(shop)) == 3


@pytest.mark.parametrize(
    ("enabled", "path", "read"),
    [(1, "/up", (True, "/up")), ("yes", 7, (None, None)), (None, None, (None, None))],
)
def test_an_odd_health_check_field_reads_as_unknown_and_keeps_the_application(enabled, path, read):
    app = CoolifyApplication.model_validate(
        {"uuid": SHOP, "health_check_enabled": enabled, "health_check_path": path}
    )
    assert (app.health_check_enabled, app.health_check_path) == read
