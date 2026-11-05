"""Group a host's containers into services (docs/design/hosts.md, "Service discovery")."""

from bleep.hosts.config import ServiceOverrides
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import Service

SELF_LABEL = "bleep.self"
COMPOSE_PROJECT = "com.docker.compose.project"


def is_self(labels: dict[str, str]) -> bool:
    """Whether these labels mark a container as Bleep's own."""
    return labels.get(SELF_LABEL) == "true"


class AmbiguousOverride(ValueError):
    """An override key matches no service key and the display names of several services."""


def _is_coolify_app(labels: dict[str, str]) -> bool:
    if "coolify.type" in labels:
        return labels["coolify.type"] == "application"
    return "coolify.applicationId" in labels


def _classify(container: ContainerSummary) -> tuple[str, str, str]:
    """(key, display name, source) of one container."""
    labels = container.labels
    project = labels.get(COMPOSE_PROJECT)
    if _is_coolify_app(labels):
        if labels.get("coolify.applicationUuid"):
            key = labels["coolify.applicationUuid"]
        elif labels.get("coolify.applicationId"):
            key = f"id:{labels['coolify.applicationId']}"
        else:
            key = f"compose:{project}" if project else f"container:{container.name}"
        name = labels.get("coolify.resourceName") or key
        pr = labels.get("coolify.pullRequestId", "0")
        if pr not in ("", "0"):
            key, name = f"{key}-pr-{pr}", f"{name} (PR {pr})"
        return key, name, "coolify"
    if project:
        return f"compose:{project}", project, "compose"
    return f"container:{container.name}", container.name, "single"


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
        key, name, source = _classify(container)
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

    services = []
    for key, (name, source, members) in groups.items():
        by_key = overrides.get(key)
        by_name = overrides.get(name) if name not in groups else None
        over = by_key or by_name or ServiceOverrides()
        protected = {c for o in (by_key, by_name) if o is not None for c in o.protected_containers}
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
            )
        )
    return services
