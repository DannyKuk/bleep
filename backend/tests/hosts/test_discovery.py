import json
from pathlib import Path

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.discovery import group_services
from bleep.schemas.docker import ContainerSummary

FIXTURES = Path(__file__).parent.parent / "fixtures" / "labels"


def captured(fixture: str, **fields) -> ContainerSummary:
    """A container carrying the labels captured from a real host."""
    data = json.loads((FIXTURES / f"{fixture}.json").read_text())
    return ContainerSummary(
        id=f"id-{fixture}",
        name=fields.pop("name", data["container"]),
        image=data["image"][0],
        state="running",
        labels=data["labels"],
        **fields,
    )


def box(name: str, labels: dict[str, str] | None = None) -> ContainerSummary:
    return ContainerSummary(
        id=f"id-{name}", name=name, image="img", state="running", labels=labels or {}
    )


def compose(name: str, project: str = "shop") -> ContainerSummary:
    return box(name, {"com.docker.compose.project": project})


def by_id(services):
    return {s.id: s for s in services}


def test_coolify_label_wins_over_compose_project():
    svcs = group_services("h", [captured("coolify-4.5.0-dkwebsite")], {})
    assert len(svcs) == 1
    svc = svcs[0]
    assert svc.source == "coolify"
    assert svc.name == "dkwebsite"
    assert svc.id == "h/vclhwoxxy60z4vk70g0vpesq"


def test_the_two_containers_of_one_app_share_a_service_keyed_by_uuid():
    svcs = group_services(
        "h",
        [captured("coolify-4.5.3-molecula-backend"), captured("coolify-4.5.3-molecula-frontend")],
        {},
    )
    assert [(s.id, s.name, len(s.containers)) for s in svcs] == [
        ("h/hfys2nogpz7wrhcoqxymnfkt", "molecula", 2)
    ]


def test_two_apps_sharing_a_resource_name_stay_separate():
    one = box("a", {"coolify.type": "application", "coolify.applicationUuid": "u1"})
    two = box("b", {"coolify.type": "application", "coolify.applicationUuid": "u2"})
    for c in (one, two):
        c.labels["coolify.resourceName"] = "web"
    svcs = group_services("h", [one, two], {})
    assert [s.id for s in svcs] == ["h/u1", "h/u2"]
    assert [s.name for s in svcs] == ["web", "web"]


def test_old_coolify_with_application_id_is_keyed_id_prefixed():
    (svc,) = group_services("h", [captured("coolify-4.1.2-applicationid")], {})
    assert svc.id == "h/id:4"
    assert svc.source == "coolify"
    assert svc.name == "easy-pointment-staticmain-cr2hzhk4nvabtad0zinjw1ev"


def test_application_id_alone_counts_as_coolify_without_type_label():
    c = box("old", {"coolify.applicationId": "7", "com.docker.compose.project": "p"})
    (svc,) = group_services("h", [c], {})
    assert (svc.id, svc.source, svc.name) == ("h/id:7", "coolify", "id:7")


def test_a_non_application_coolify_type_is_not_an_app():
    db = box(
        "db",
        {
            "coolify.type": "database",
            "coolify.applicationId": "9",
            "com.docker.compose.project": "p",
        },
    )
    (svc,) = group_services("h", [db], {})
    assert (svc.id, svc.source) == ("h/p", "compose")


def test_preview_deployment_gets_its_own_service():
    app = captured("coolify-4.5.0-dkwebsite")
    preview = captured("coolify-4.5.0-dkwebsite", name="preview-container")
    preview.labels = preview.labels | {"coolify.pullRequestId": "12"}
    svcs = by_id(group_services("h", [app, preview], {}))
    assert set(svcs) == {"h/vclhwoxxy60z4vk70g0vpesq", "h/vclhwoxxy60z4vk70g0vpesq-pr-12"}
    assert svcs["h/vclhwoxxy60z4vk70g0vpesq-pr-12"].name == "dkwebsite (PR 12)"
    assert svcs["h/vclhwoxxy60z4vk70g0vpesq-pr-12"].source == "coolify"


def test_coolify_internal_containers_fall_back_to_compose_then_name():
    svcs = by_id(
        group_services(
            "h",
            [
                captured("coolify-app"),
                captured("coolify-proxy"),
                captured("coolify-sentinel"),
            ],
            {},
        )
    )
    assert set(svcs) == {"h/source", "h/coolify-proxy", "h/coolify-sentinel"}
    assert svcs["h/source"].source == "compose"
    assert svcs["h/coolify-sentinel"].source == "single"


def test_compose_project_groups_containers():
    svcs = group_services(
        "h", [compose("shop-web-1"), compose("shop-db-1"), compose("shop-q-1")], {}
    )
    assert [s.name for s in svcs] == ["shop"]
    assert len(svcs[0].containers) == 3
    assert svcs[0].source == "compose"


def test_unlabelled_container_is_its_own_service():
    (svc,) = group_services("h", [box("redis")], {})
    assert (svc.id, svc.name, svc.source) == ("h/redis", "redis", "single")


def test_bleep_self_containers_are_excluded():
    own = box("bleep-backend-1", {"bleep.self": "true", "com.docker.compose.project": "shop"})
    alone = box("bleep-ui", {"bleep.self": "true"})
    svcs = group_services("h", [own, alone, compose("shop-web-1")], {})
    assert [c.name for s in svcs for c in s.containers] == ["shop-web-1"]


def test_overrides_attach_urls_rules_and_protected():
    over = ServiceOverrides(
        urls=[UrlCheckConfig(url="https://shop.example")],
        repo="me/shop",
        docs_paths=["README.md"],
        protected_containers=["shop-db-1"],
        rules=["never restart the db"],
    )
    (svc,) = group_services("h", [compose("shop-web-1"), compose("shop-db-1")], {"shop": over})
    assert svc.protected == {"shop-db-1"}
    assert svc.repo == "me/shop"
    assert svc.rules == ["never restart the db"]
    assert svc.docs_paths == ["README.md"]
    assert [u.url for u in svc.urls] == ["https://shop.example"]


def test_override_matches_display_name_when_the_key_is_a_uuid():
    over = ServiceOverrides(repo="me/dkwebsite")
    (svc,) = group_services("h", [captured("coolify-4.5.0-dkwebsite")], {"dkwebsite": over})
    assert svc.repo == "me/dkwebsite"


def test_override_key_wins_over_display_name_on_conflict():
    by_key = ServiceOverrides(repo="key/repo")
    by_name = ServiceOverrides(repo="name/repo")
    (svc,) = group_services(
        "h",
        [captured("coolify-4.5.0-dkwebsite")],
        {"dkwebsite": by_name, "vclhwoxxy60z4vk70g0vpesq": by_key},
    )
    assert svc.repo == "key/repo"


def test_preview_does_not_inherit_the_apps_override():
    preview = captured("coolify-4.5.0-dkwebsite")
    preview.labels = preview.labels | {"coolify.pullRequestId": "3"}
    (svc,) = group_services(
        "h", [preview], {"dkwebsite": ServiceOverrides(protected_containers=["x"])}
    )
    assert svc.protected == set()
