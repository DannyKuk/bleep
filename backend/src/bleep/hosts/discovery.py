"""Group a host's containers into services (docs/design/hosts.md, "Service discovery")."""

from bleep.hosts.config import ServiceOverrides
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import Service

SELF_LABEL = "bleep.self"
COMPOSE_PROJECT = "com.docker.compose.project"


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


def group_services(
    host_id: str,
    containers: list[ContainerSummary],
    overrides: dict[str, ServiceOverrides],
) -> list[Service]:
    """Group containers into services, in order of first appearance. Pure."""
    groups: dict[str, tuple[str, str, list[ContainerSummary]]] = {}
    for container in containers:
        if container.labels.get(SELF_LABEL) == "true":
            continue
        key, name, source = _classify(container)
        if key not in groups:
            groups[key] = (name, source, [])
        groups[key][2].append(container)

    by_key: dict[str, ServiceOverrides] = {}
    for okey, over in overrides.items():
        if okey in groups:
            by_key[okey] = over
            continue
        named = [k for k, (name, _, _) in groups.items() if name == okey and k not in overrides]
        if len(named) > 1:
            raise AmbiguousOverride(
                f"host {host_id}: override {okey!r} matches the display name of several "
                f"services ({', '.join(named)}); use one of those keys instead"
            )
        if named:
            by_key[named[0]] = over

    services = []
    for key, (name, source, members) in groups.items():
        over = by_key.get(key) or ServiceOverrides()
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
                protected=set(over.protected_containers),
            )
        )
    return services
