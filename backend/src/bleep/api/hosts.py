"""Read routes for the overview and a service's page: hosts and the services on them."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from bleep.api.deps import get_parts
from bleep.incidents.repository import utcnow
from bleep.schemas.api import (
    BasicsView,
    ContainerView,
    DiskView,
    HostView,
    ServiceDetail,
    ServiceView,
)
from bleep.schemas.docker import HostBasics
from bleep.schemas.incidents import Subject
from bleep.schemas.services import Service
from bleep.watcher.health import (
    container_health,  # noqa: F401  (tests import it from here)
    host_health,
    service_health,
    url_status,
)
from bleep.watcher.http_checks import UrlCheck
from bleep.wiring import AppParts

router = APIRouter(tags=["hosts"])

Checks = dict[tuple[str, str], tuple[UrlCheck, datetime]]


async def _stored_checks(parts: AppParts, host_id: str) -> Checks:
    return {
        (sid, check.url): (check, at) for sid, check, at in await parts.url_checks.latest(host_id)
    }


def _view(
    service: Service, open_incident_id: str | None, checks: Checks, now: datetime
) -> ServiceView:
    urls = [url_status(u.url, checks.get((service.id, u.url)), now) for u in service.urls]
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
    now = utcnow()
    checks: dict[str, Checks] = {}
    views = []
    for service in parts.registry.services(host_id):
        if service.host_id not in checks:
            checks[service.host_id] = await _stored_checks(parts, service.host_id)
        subject = Subject(kind="service", host_id=service.host_id, service_id=service.id)
        open_id = await parts.incidents.open_incident_id(subject)
        views.append(_view(service, open_id, checks[service.host_id], now))
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
        service=_view(
            service,
            await parts.incidents.open_incident_id(subject),
            await _stored_checks(parts, service.host_id),
            utcnow(),
        ),
        rules=list(service.rules),
        host_rules=list(host.config.rules),
    )
