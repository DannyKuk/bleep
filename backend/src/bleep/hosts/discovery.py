"""Group a host's containers into services (docs/design/hosts.md, "Service discovery")."""

from collections.abc import Collection

from bleep.hosts.config import EVERY_CONTAINER, ServiceOverrides
from bleep.hosts.coolify_names import app_protected_by
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import Service

SELF_LABEL = "bleep.self"
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_CONFIG_HASH = "com.docker.compose.config-hash"


def is_self(labels: dict[str, str]) -> bool:
    """Whether these labels mark a container as Bleep's own."""
    return labels.get(SELF_LABEL) == "true"


def protected_by(container: ContainerSummary, protected: Collection[str]) -> str | None:
    """The entry of `protected` that protects `container`: its own name, else the entry that
    names its Coolify application (`app_protected_by`); None when it is not protected. The plan
    check refuses by it and discovery shows the lock by it, so the two cannot disagree."""
    if container.name in protected:
        return container.name
    return app_protected_by(container, protected)


def expects_exit(entries: list[str], name: str, labels: dict[str, str]) -> bool:
    """Whether a service's `exits` entries name this container: by its name, by its Compose
    service (so every `compose run --rm` container of `fetch` matches), or all of them (`*`)."""
    service = compose_service(labels)
    return any(e in (EVERY_CONTAINER, name) or e == service for e in entries)


def service_key(name: str, labels: dict[str, str]) -> str:
    """The key of the service a container with these labels belongs to."""
    return _classify(name, labels)[0]


class AmbiguousOverride(ValueError):
    """An override key matches no service key and the display names of several services."""


def _is_coolify_app(labels: dict[str, str]) -> bool:
    if "coolify.type" in labels:
        return labels["coolify.type"] == "application"
    return "coolify.applicationId" in labels


def compose_project(labels: dict[str, str]) -> str | None:
    """The Compose project of a container Compose created, else None.

    `docker compose build` labels the image with its project and service, and a container
    `docker run` starts from that image carries them too. Only a container Compose created (`up`
    or `run`) also has the config hash, and Compose lists a project's containers by it."""
    if COMPOSE_CONFIG_HASH not in labels:
        return None
    return labels.get(COMPOSE_PROJECT) or None


def compose_service(labels: dict[str, str]) -> str | None:
    """The Compose service of a container Compose created, else None (see `compose_project`;
    an image-only container inherits the service label of the container it was built for)."""
    if COMPOSE_CONFIG_HASH not in labels:
        return None
    return labels.get(COMPOSE_SERVICE) or None


def _classify(container_name: str, labels: dict[str, str]) -> tuple[str, str, str]:
    """(key, display name, source) of one container."""
    project = compose_project(labels)
    if _is_coolify_app(labels):
        if labels.get("coolify.applicationUuid"):
            key = labels["coolify.applicationUuid"]
        elif labels.get("coolify.applicationId"):
            key = f"id:{labels['coolify.applicationId']}"
        else:
            key = f"compose:{project}" if project else f"container:{container_name}"
        name = labels.get("coolify.resourceName") or key
        pr = labels.get("coolify.pullRequestId", "0")
        if pr not in ("", "0"):
            key, name = f"{key}-pr-{pr}", f"{name} (PR {pr})"
        return key, name, "coolify"
    if project:
        return f"compose:{project}", project, "compose"
    return f"container:{container_name}", container_name, "single"


def _named(
    groups: dict[str, tuple[str, str, list[ContainerSummary]]],
    name: str,
    overrides: dict[str, ServiceOverrides],
) -> list[str]:
    """The keys of the services called `name` that have no override under their own key."""
    return [k for k, (n, _, _) in groups.items() if n == name and k not in overrides]


def group_services(
    host_id: str,
    containers: list[ContainerSummary],
    overrides: dict[str, ServiceOverrides],
) -> list[Service]:
    """Group containers into services, in order of first appearance. Pure."""
    groups: dict[str, tuple[str, str, list[ContainerSummary]]] = {}
    for container in containers:
        if is_self(container.labels):
            continue
        key, name, source = _classify(container.name, container.labels)
        if key not in groups:
            groups[key] = (name, source, [])
        groups[key][2].append(container)

    for okey in overrides:
        unclaimed = [] if okey in groups else _named(groups, okey, overrides)
        if len(unclaimed) > 1:
            raise AmbiguousOverride(
                f"host {host_id}: override {okey!r} matches the display name of several "
                f"services ({', '.join(unclaimed)}); use one of those keys instead"
            )

    # The guard refuses a member any override's entry protects (`protected_by`), by its name or
    # its Coolify application's, so it is shown protected whichever override lists the entry.
    anywhere = {c for o in overrides.values() for c in o.protected_containers}
    services = []
    for key, (name, source, members) in groups.items():
        by_key = overrides.get(key)
        by_name = overrides.get(name) if name not in groups else None
        over = by_key or by_name or ServiceOverrides()
        protected = {c for o in (by_key, by_name) if o is not None for c in o.protected_containers}
        protected |= {c.name for c in members if protected_by(c, anywhere) is not None}
        services.append(
            Service(
                id=f"{host_id}/{key}",
                host_id=host_id,
                name=name,
                source=source,  # type: ignore[arg-type]
                containers=members,
                urls=over.urls,
                repo=over.repo,
                docs_paths=over.docs_paths,
                rules=over.rules,
                protected=protected,
                exits=over.exits,
            )
        )
    return services
