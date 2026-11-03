"""Read routes for the overview and a service's page: hosts and the services on them."""

from collections.abc import Iterable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from bleep.api.deps import get_parts
from bleep.schemas.api import (
    BasicsView,
    ContainerView,
    DiskView,
    Health,
    HostView,
    ServiceDetail,
    ServiceView,
    UrlStatus,
)
from bleep.schemas.docker import ContainerSummary, HostBasics
from bleep.schemas.incidents import Subject
from bleep.schemas.services import Service
from bleep.wiring import AppParts

router = APIRouter(tags=["hosts"])

_RANK: dict[Health, int] = {"unknown": 0, "ok": 1, "stopped": 1, "degraded": 2, "down": 3}

# Exit codes of a process that was asked to stop (0, or killed by SIGTERM). 137 is left out: it
# is also what the OOM killer leaves, so it counts as a crash.
CLEAN_EXITS = (0, 143)


def _worst(healths: Iterable[Health]) -> Health:
    return max(healths, key=_RANK.__getitem__, default="unknown")


def is_finished(container: ContainerSummary) -> bool:
    """A one-shot that ran to the end: exited 0 and no restart policy to bring it back."""
    return (
        container.state == "exited"
        and container.exit_code == 0
        and container.restart_policy in ("", "no")
    )


def is_stopped(container: ContainerSummary) -> bool:
    """Exited cleanly, so a human (or a job's end) did it, not a crash."""
    return container.state == "exited" and container.exit_code in CLEAN_EXITS


def container_health(container: ContainerSummary) -> Health:
    """One container's health: running and healthy (or no healthcheck) is ok; starting or
    restarting is degraded; a clean exit is stopped; unhealthy, crashed, dead, created and
    paused are down."""
    if container.state == "running":
        return {"healthy": "ok", "none": "ok", "starting": "degraded", "unhealthy": "down"}[
            container.health
        ]
    if container.state == "restarting":
        return "degraded"
    if is_stopped(container):
        return "stopped"
    if container.state in ("exited", "dead", "created", "paused"):
        return "down"
    return "unknown"  # a state Bleep has no rule for, such as removing


def service_health(containers: Iterable[ContainerSummary], url_statuses: Iterable[str]) -> Health:
    """The worst of its containers and its URLs (a failing URL is down); unknown with no data.

    Finished one-shots (exit 0, no restart policy) are left out while anything else is there.
    When everything has exited cleanly the service is `stopped`, whatever its URLs say.
    """
    members = list(containers)
    active = [c for c in members if not is_finished(c)]
    if members and not active:
        return "stopped"
    healths: list[Health] = [container_health(c) for c in active]
    if healths and all(h == "stopped" for h in healths):
        return "stopped"
    # One container stopped on purpose among running ones leaves the service partly working.
    healths = ["degraded" if h == "stopped" else h for h in healths]
    from_urls: list[Health] = [
        {"ok": "ok", "failing": "down"}.get(status, "unknown") for status in url_statuses
    ]
    return _worst([*healths, *from_urls])


def host_health(reachable: bool, last_seen: datetime | None, services: Iterable[Health]) -> Health:
    """Unknown when unreachable with no snapshot, down when unreachable, else the worst service
    (ok when it has none; a stopped service counts as ok)."""
    if not reachable:
        return "unknown" if last_seen is None else "down"
    healths: list[Health] = ["ok" if h == "stopped" else h for h in services]
    return _worst(healths) if healths else "ok"


def _view(service: Service, open_incident_id: str | None) -> ServiceView:
    # URL checks arrive with the watcher; until it records one, a configured URL is unknown.
    urls = [
        UrlStatus(url=u.url, status="unknown", last_checked=None, status_code=None, latency_ms=None)
        for u in service.urls
    ]
    return ServiceView(
        id=service.id,
        host_id=service.host_id,
        name=service.name,
        source=service.source,
        health=service_health(service.containers, (u.status for u in urls)),
        containers=[
            ContainerView(
                id=c.id,
                name=c.name,
                image=c.image,
                state=c.state,
                health=c.health,
                restart_count=c.restart_count,
                exit_code=c.exit_code,
                protected=c.name in service.protected,
            )
            for c in service.containers
        ],
        urls=urls,
        open_incident_id=open_incident_id,
    )


async def _service_views(parts: AppParts, host_id: str | None) -> list[ServiceView]:
    if host_id is not None and host_id not in {h.config.id for h in parts.registry.hosts()}:
        return []
    views = []
    for service in parts.registry.services(host_id):
        subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
        views.append(_view(service, await parts.incidents.open_incident_id(subject)))
    return views


def _basics(basics: HostBasics | None) -> BasicsView | None:
    if basics is None:
        return None
    return BasicsView(
        **basics.model_dump(exclude={"disks"}),
        disks=[DiskView(mount=d.mount, used_percent=d.used_percent) for d in basics.disks],
    )


@router.get("/hosts")
async def list_hosts(parts: Annotated[AppParts, Depends(get_parts)]) -> list[HostView]:
    result = []
    for state in parts.registry.hosts():
        views = await _service_views(parts, state.config.id)
        result.append(
            HostView(
                id=state.config.id,
                name=state.config.name,
                kind=state.config.kind,
                reachable=state.reachable,
                last_seen=state.last_seen,
                error=state.error,
                basics=_basics(state.basics),
                service_count=len(views),
                health=host_health(state.reachable, state.last_seen, (v.health for v in views)),
            )
        )
    return result


@router.get("/services")
async def list_services(
    parts: Annotated[AppParts, Depends(get_parts)], host_id: str | None = None
) -> list[ServiceView]:
    return await _service_views(parts, host_id)


@router.get("/services/{service_id:path}")
async def get_service(
    service_id: str, parts: Annotated[AppParts, Depends(get_parts)]
) -> ServiceDetail:
    """One service as the overview shows it, with its own rules and its host's.

    A service id holds a `/` (`<host>/<key>`), so this route takes the rest of the path; the facts
    routes under `/services/{id}/facts` are registered before it (`main.create_app`).
    """
    service = parts.registry.service(service_id)
    host = (
        next((h for h in parts.registry.hosts() if h.config.id == service.host_id), None)
        if service is not None
        else None
    )
    if service is None or host is None:
        raise HTTPException(404, "no such service")
    subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
    return ServiceDetail(
        service=_view(service, await parts.incidents.open_incident_id(subject)),
        rules=list(service.rules),
        host_rules=list(host.config.rules),
    )
