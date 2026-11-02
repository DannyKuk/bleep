"""Read routes for the overview: hosts and the services on them."""

from collections.abc import Iterable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from bleep.api.deps import get_parts
from bleep.schemas.api import ContainerView, Health, HostView, ServiceView, UrlStatus
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.incidents import Subject
from bleep.schemas.services import Service
from bleep.wiring import AppParts

router = APIRouter(tags=["hosts"])

_RANK: dict[Health, int] = {"unknown": 0, "ok": 1, "degraded": 2, "down": 3}


def _worst(healths: Iterable[Health]) -> Health:
    return max(healths, key=_RANK.__getitem__, default="unknown")


def container_health(container: ContainerSummary) -> Health:
    """One container's health: running and healthy (or no healthcheck) is ok; starting or
    restarting is degraded; unhealthy, exited, dead, created and paused are down."""
    if container.state == "running":
        return {"healthy": "ok", "none": "ok", "starting": "degraded", "unhealthy": "down"}[
            container.health
        ]
    if container.state == "restarting":
        return "degraded"
    if container.state in ("exited", "dead", "created", "paused"):
        return "down"
    return "unknown"  # a state Bleep has no rule for, such as removing


def service_health(containers: Iterable[ContainerSummary], url_statuses: Iterable[str]) -> Health:
    """The worst of its containers and its URLs (a failing URL is down); unknown with no data."""
    from_urls: list[Health] = [
        {"ok": "ok", "failing": "down"}.get(status, "unknown") for status in url_statuses
    ]
    return _worst([*map(container_health, containers), *from_urls])


def host_health(reachable: bool, last_seen: datetime | None, services: Iterable[Health]) -> Health:
    """Unknown when unreachable with no snapshot, down when unreachable, else the worst service
    (ok when it has none)."""
    if not reachable:
        return "unknown" if last_seen is None else "down"
    healths = list(services)
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
                # Task 6 adds restart_count to ContainerSummary; 0 until then.
                restart_count=getattr(c, "restart_count", 0),
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
                basics=None,  # the host probe fills this in a later task
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
