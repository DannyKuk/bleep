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
from fakes import CoolifyAnswers, compose_labels
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
        c.expect_status == 204 and c.body_contains is None for c in suggested_checks(shop, apps())
    )


def statuses(service: Service, applications: list[CoolifyApplication]) -> set[int]:
    return {check.expect_status for check in suggested_checks(service, applications)}


def test_a_check_on_the_health_path_expects_the_status_coolify_expects_and_slash_expects_200():
    blog = service_of(box(f"{BLOG}-074501234567", labels=coolify_labels(BLOG, "blog")))
    notes = service_of(box(f"{NOTES}-074501234567", labels=coolify_labels(NOTES, "notes")))
    # Both apps carry a return code of 204, but their health check is off or its path no path.
    assert statuses(blog, apps()) == statuses(notes, apps()) == {200}


@pytest.mark.parametrize("code", [None, "204", 99, 600, True, 2.5])
def test_a_health_path_without_a_usable_return_code_expects_200(code):
    raw = json.loads((FIXTURES / "domains.json").read_text(encoding="utf-8"))
    raw[0]["health_check_return_code"] = code
    shop = service_of(box(f"{SHOP}-074501234567", labels=coolify_labels(SHOP, "shop")))
    applications = [CoolifyApplication.model_validate(item) for item in raw]
    assert urls(suggested_checks(shop, applications))[0].endswith("/health")
    assert statuses(shop, applications) == {200}


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
        box(f"{FORUM}-0745", labels=coolify_labels(FORUM, "forum")),  # no Compose service label
        box("other-0745", labels=coolify_labels("gone0uuid000000000000099", "gone")),
    ],
)
def test_a_service_without_a_coolify_app_with_domains_is_offered_nothing(container):
    assert suggested_checks(service_of(container), apps()) == []


def compose_app_service(*compose_services: str, urls: list[str] = ()) -> Service:
    """The service of the `forum` Compose application with a container per Compose service, as
    Coolify names and labels them."""
    return service_of(
        *(
            box(
                f"{name}-{FORUM}-074501234567",
                labels=coolify_labels(FORUM, "forum") | compose_labels(FORUM, name),
            )
            for name in compose_services
        ),
        urls=urls,
    )


def test_a_compose_service_is_offered_the_domains_coolify_keeps_for_the_services_it_runs():
    checks = suggested_checks(compose_app_service("web", "api", "worker", "admin"), apps())
    assert urls(checks) == [
        "https://forum.example.com/",
        "https://www.forum.example.com/",
        "https://api.forum.example.com/v1/",
        "https://admin.forum.example.com/",
    ]
    # The app's health check (here on, at /health, expecting 204) describes no Compose service.
    assert all(c.expect_status == 200 and c.body_contains is None for c in checks)


@pytest.mark.parametrize(
    ("running", "offered"),
    [
        (["web"], ["https://forum.example.com/", "https://www.forum.example.com/"]),
        (["api"], ["https://api.forum.example.com/v1/"]),
        (["worker"], []),  # its domain is empty
        (["db"], []),  # Coolify keeps no domain for it
        (["Web"], []),  # a Compose service name is exact
    ],
)
def test_only_the_domains_of_the_compose_services_the_service_runs_are_offered(running, offered):
    assert urls(suggested_checks(compose_app_service(*running), apps())) == offered


def test_userinfo_preview_non_http_and_query_domains_of_a_compose_service_are_left_out():
    suggested = urls(suggested_checks(compose_app_service("admin"), apps()))
    assert suggested == ["https://admin.forum.example.com/"]
    assert not any("fake-compose-pass" in url for url in suggested)


def test_a_compose_label_without_the_config_hash_names_no_compose_service():
    labels = coolify_labels(FORUM, "forum") | {"com.docker.compose.service": "web"}
    forum = service_of(box(f"web-{FORUM}-074501234567", labels=labels))
    assert suggested_checks(forum, apps()) == []


def test_checks_a_compose_service_has_are_not_offered_again():
    forum = compose_app_service("web", urls=["https://WWW.forum.example.com"])
    assert urls(suggested_checks(forum, apps())) == ["https://forum.example.com/"]


def test_a_preview_of_a_compose_app_is_offered_nothing():
    labels = coolify_labels(FORUM, "forum", pr="3") | compose_labels(FORUM, "web")
    preview = service_of(box(f"web-{FORUM}-pr-3-0745", labels=labels))
    assert suggested_checks(preview, apps()) == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        '"web"',
        '{"web": "https://a.example.com"}',
        '{"web": {"domain": ["https://a.example.com"]}}',
        '{"web": {"domain": null}}',
        '{"we b": {"domain": "https://a.example.com"}}',
        '{"web": {"domain": "https://a.example.com"}}' + " " * 16_000,
        "[" * 100_000,
    ],
)
def test_a_compose_field_that_is_not_what_coolify_writes_offers_nothing(raw):
    odd = apps()[-1].model_copy(update={"docker_compose_domains": raw})
    assert suggested_checks(compose_app_service("web", "we b"), [odd]) == []


def test_an_odd_compose_service_does_not_hide_the_others():
    raw = '{"web": 7, "api": {"domain": "https://api.forum.example.com"}}'
    odd = apps()[-1].model_copy(update={"docker_compose_domains": raw})
    forum = compose_app_service("web", "api")
    assert urls(suggested_checks(forum, [odd])) == ["https://api.forum.example.com/"]


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
    forum = compose_app_service("web", "api")
    assert len(await plugin.suggested_checks(context(), forum)) == 3


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


@pytest.mark.parametrize(
    ("code", "read"),
    [(204, 204), (404, 404), ("204", None), (700, None), (True, None), (None, None)],
)
def test_an_odd_health_check_return_code_reads_as_unknown_and_keeps_the_application(code, read):
    app = CoolifyApplication.model_validate({"uuid": SHOP, "health_check_return_code": code})
    assert app.health_check_return_code == read
