import json
from pathlib import Path

import pytest

from bleep.hosts.config import ServiceOverrides, UrlCheckConfig
from bleep.hosts.discovery import (
    AmbiguousOverride,
    compose_service,
    expects_exit,
    group_services,
)
from bleep.schemas.docker import ContainerSummary
from fakes import compose_labels

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
    return box(name, compose_labels(project))


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
        }
        | compose_labels("p"),
    )
    (svc,) = group_services("h", [db], {})
    assert (svc.id, svc.source) == ("h/compose:p", "compose")


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
    assert set(svcs) == {
        "h/compose:source",
        "h/compose:coolify-proxy",
        "h/container:coolify-sentinel",
    }
    assert svcs["h/compose:source"].source == "compose"
    assert svcs["h/container:coolify-sentinel"].source == "single"


def test_compose_project_groups_containers():
    svcs = group_services(
        "h", [compose("shop-web-1"), compose("shop-db-1"), compose("shop-q-1")], {}
    )
    assert [s.name for s in svcs] == ["shop"]
    assert len(svcs[0].containers) == 3
    assert svcs[0].source == "compose"


def test_a_container_run_from_a_compose_built_image_is_its_own_service():
    """`docker compose build` labels the image with its project and service, and `docker run`
    copies them onto the container; only a container Compose created has the config hash."""
    pair = [captured("compose-shop-proxy"), captured("image-only-shop-canary")]
    svcs = by_id(group_services("h", pair, {}))
    assert set(svcs) == {"h/compose:shop", "h/container:shop-canary"}
    assert [c.name for c in svcs["h/compose:shop"].containers] == ["shop-proxy"]
    canary = svcs["h/container:shop-canary"]
    assert (canary.name, canary.source) == ("shop-canary", "single")


def test_a_compose_override_still_reaches_every_container_compose_created():
    """Protected names under the key and the name (#230), rules and URLs all still apply to the
    Compose-created containers; the canary takes none of the shop's settings."""
    db = captured("compose-shop-proxy", name="shop-db")
    db.labels = db.labels | {"com.docker.compose.service": "shop-db"}
    run = captured("compose-shop-proxy", name="shop-proxy-run-3f2a")
    run.labels = run.labels | {"com.docker.compose.oneoff": "True"}
    run.labels.pop("com.docker.compose.container-number")
    containers = [captured("compose-shop-proxy"), db, run, captured("image-only-shop-canary")]
    overrides = {
        "shop": ServiceOverrides(protected_containers=["shop-db"]),
        "compose:shop": ServiceOverrides(
            urls=[UrlCheckConfig(url="https://shop.example")],
            rules=["never restart the db"],
            protected_containers=["shop-proxy"],
        ),
    }
    svcs = by_id(group_services("h", containers, overrides))
    shop = svcs["h/compose:shop"]
    assert [c.name for c in shop.containers] == ["shop-proxy", "shop-db", "shop-proxy-run-3f2a"]
    assert shop.protected == {"shop-db", "shop-proxy"}
    assert shop.rules == ["never restart the db"]
    assert [u.url for u in shop.urls] == ["https://shop.example"]
    canary = svcs["h/container:shop-canary"]
    assert (canary.rules, canary.urls, canary.protected) == ([], [], set())


def test_a_container_that_left_its_compose_group_keeps_the_protection_its_name_has():
    """The hosts file protects by container name, wherever the override sits, so the canary is
    still shown protected when the shop's override names it."""
    overrides = {"shop": ServiceOverrides(protected_containers=["shop-canary"])}
    svcs = by_id(
        group_services(
            "h", [captured("compose-shop-proxy"), captured("image-only-shop-canary")], overrides
        )
    )
    assert svcs["h/container:shop-canary"].protected == {"shop-canary"}
    assert svcs["h/compose:shop"].protected == {"shop-canary"}


def test_bleep_own_container_is_excluded_without_compose_labels_too():
    own = captured("image-only-shop-canary", name="bleep-probe")
    own.labels = own.labels | {"bleep.self": "true"}
    assert group_services("h", [own], {}) == []


def test_unlabelled_container_is_its_own_service():
    (svc,) = group_services("h", [box("redis")], {})
    assert (svc.id, svc.name, svc.source) == ("h/container:redis", "redis", "single")


def test_bleep_self_containers_are_excluded():
    own = box("bleep-backend-1", {"bleep.self": "true"} | compose_labels("shop"))
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


OLD_UUID = "cr2hzhk4nvabtad0zinjw1ev"  # Coolify 4.1.2 wrote no coolify.applicationUuid for it


def old_coolify(name: str) -> ContainerSummary:
    """A Coolify 4.1.2 container called `name` whose `coolify.name` label no longer holds the
    uuid, so only its container name can tie it to the uuid."""
    container = captured("coolify-4.1.2-applicationid", name=name)
    container.labels = container.labels | {"coolify.name": "static-site"}
    return container


@pytest.mark.parametrize(
    ("fixture", "entry"),
    [
        ("coolify-4.5.0-dkwebsite", "vclhwoxxy60z4vk70g0vpesq"),  # by uuid
        ("coolify-4.5.0-dkwebsite", "dkwebsite"),  # by resource name
        ("coolify-4.5.3-molecula-backend", "Molecula"),  # by the name as a person writes it
        ("coolify-4.1.2-applicationid", OLD_UUID),  # by the uuid in the 4.1.x container name
    ],
)
def test_a_coolify_apps_containers_show_protected_when_its_uuid_or_name_is(fixture, entry):
    container = captured(fixture)
    # An override key no service has: its list protects the host, as the plan check reads it.
    (svc,) = group_services(
        "h", [container], {"other": ServiceOverrides(protected_containers=[entry])}
    )
    assert container.name in svc.protected


def test_a_coolify_4_1_container_shows_protected_by_the_uuid_in_its_anchored_name():
    container = old_coolify(f"{OLD_UUID}-000205002169")
    (svc,) = group_services(
        "h", [container], {"other": ServiceOverrides(protected_containers=[OLD_UUID])}
    )
    assert container.name in svc.protected


@pytest.mark.parametrize(
    ("container", "entry"),
    [
        (captured("coolify-4.5.0-dkwebsite"), "vclhwoxxy60z4vk70g0vpes"),  # a uuid prefix
        (captured("coolify-4.5.0-dkwebsite"), "VCLHWOXXY60Z4VK70G0VPESQ"),  # exact case
        (captured("coolify-4.5.0-dkwebsite"), "dkwebsit"),
        (captured("coolify-4.5.0-dkwebsite"), "dkwebsite-2"),
        (captured("coolify-4.1.2-applicationid"), "4"),  # its coolify.applicationId
        (old_coolify(f"{OLD_UUID}-000205002169-old"), OLD_UUID),  # the name is not anchored
        (old_coolify(f"x{OLD_UUID}-000205002169"), OLD_UUID),  # the uuid is not a whole part
        (box(f"{OLD_UUID}-000205002169"), OLD_UUID),  # no Coolify labels
    ],
)
def test_a_container_that_only_resembles_an_entry_is_not_shown_protected(container, entry):
    (svc,) = group_services(
        "h", [container], {"other": ServiceOverrides(protected_containers=[entry])}
    )
    assert container.name not in svc.protected


def test_single_container_and_compose_project_with_one_name_stay_separate():
    svcs = group_services("h", [box("app"), compose("app-web-1", "app")], {})
    assert sorted(s.id for s in svcs) == ["h/compose:app", "h/container:app"]
    assert {s.name for s in svcs} == {"app"}


def test_compose_project_named_like_a_uuid_does_not_merge_with_the_coolify_app():
    uuid = "vclhwoxxy60z4vk70g0vpesq"
    svcs = group_services(
        "h",
        [captured("coolify-4.5.0-dkwebsite"), compose("x-1", uuid), compose("y-1", "z-pr-1")],
        {},
    )
    assert sorted(s.id for s in svcs) == [f"h/compose:{uuid}", "h/compose:z-pr-1", f"h/{uuid}"]


def test_override_by_name_matching_two_services_is_an_error():
    with pytest.raises(AmbiguousOverride, match="compose:app"):
        group_services("h", [box("app"), compose("app-web-1", "app")], {"app": ServiceOverrides()})


def test_override_by_key_disambiguates_equal_names():
    svcs = group_services(
        "h",
        [box("app"), compose("app-web-1", "app")],
        {"compose:app": ServiceOverrides(repo="me/app")},
    )
    assert {s.id: s.repo for s in svcs} == {"h/compose:app": "me/app", "h/container:app": None}


def test_a_name_override_still_protects_when_the_key_has_one_too():
    by_name = ServiceOverrides(protected_containers=["shop-db-1"])
    by_key = ServiceOverrides(urls=[UrlCheckConfig(url="https://shop.example")])
    (svc,) = group_services(
        "h",
        [compose("shop-web-1"), compose("shop-db-1")],
        {"shop": by_name, "compose:shop": by_key},
    )
    assert svc.protected == {"shop-db-1"}
    assert [u.url for u in svc.urls] == ["https://shop.example"]


def test_a_key_override_still_protects_when_the_name_has_one_too():
    by_key = ServiceOverrides(protected_containers=["shop-db-1"])
    by_name = ServiceOverrides(protected_containers=[])
    (svc,) = group_services(
        "h",
        [compose("shop-web-1"), compose("shop-db-1")],
        {"compose:shop": by_key, "shop": by_name},
    )
    assert svc.protected == {"shop-db-1"}


def test_protected_containers_of_the_key_and_the_name_are_unioned():
    (svc,) = group_services(
        "h",
        [compose("shop-web-1"), compose("shop-db-1")],
        {
            "shop": ServiceOverrides(protected_containers=["shop-db-1"]),
            "compose:shop": ServiceOverrides(protected_containers=["shop-web-1"]),
        },
    )
    assert svc.protected == {"shop-db-1", "shop-web-1"}


def test_urls_repo_docs_and_rules_come_from_the_key_when_both_match():
    by_name = ServiceOverrides(
        urls=[UrlCheckConfig(url="https://name.example")],
        repo="name/repo",
        docs_paths=["NAME.md"],
        rules=["name rule"],
    )
    by_key = ServiceOverrides(
        urls=[UrlCheckConfig(url="https://key.example")],
        repo="key/repo",
        docs_paths=["KEY.md"],
        rules=["key rule"],
    )
    (svc,) = group_services("h", [compose("shop-web-1")], {"shop": by_name, "compose:shop": by_key})
    assert [u.url for u in svc.urls] == ["https://key.example"]
    assert (svc.repo, svc.docs_paths, svc.rules) == ("key/repo", ["KEY.md"], ["key rule"])


def test_a_name_shared_with_a_key_matched_service_still_goes_to_the_other_one():
    svcs = by_id(
        group_services(
            "h",
            [box("app"), compose("app-web-1", "app")],
            {
                "app": ServiceOverrides(repo="name/app", protected_containers=["app"]),
                "compose:app": ServiceOverrides(repo="key/app"),
            },
        )
    )
    assert svcs["h/container:app"].repo == "name/app"
    assert svcs["h/container:app"].protected == {"app"}
    assert svcs["h/compose:app"].repo == "key/app"


def test_exits_names_a_container_by_its_name_its_compose_service_or_all():
    job = compose_labels("molecula", "fetch")
    assert expects_exit(["fetch"], "molecula-fetch-run-3f2a", job)  # every `compose run --rm`
    assert expects_exit(["molecula-fetch-1"], "molecula-fetch-1", {})
    assert expects_exit(["*"], "anything", {})
    assert not expects_exit(["fetch"], "molecula-web-1", compose_labels("molecula", "web"))
    assert not expects_exit([], "molecula-fetch-1", job)


def test_a_container_compose_did_not_create_is_matched_by_its_name_not_its_inherited_service():
    canary = captured("image-only-shop-canary")
    assert canary.labels["com.docker.compose.service"] == "shop-proxy"  # inherited from the image
    assert compose_service(canary.labels) is None
    assert not expects_exit(["shop-proxy"], canary.name, canary.labels)
    assert expects_exit(["shop-canary"], canary.name, canary.labels)
    proxy = captured("compose-shop-proxy")
    assert compose_service(proxy.labels) == "shop-proxy"
    assert expects_exit(["shop-proxy"], proxy.name, proxy.labels)


def test_a_service_takes_exits_from_its_override_like_its_urls():
    labels = compose_labels("molecula")
    [service] = group_services(
        "h", [box("molecula-fetch-1", labels)], {"molecula": ServiceOverrides(exits=["fetch"])}
    )
    assert service.exits == ["fetch"]
