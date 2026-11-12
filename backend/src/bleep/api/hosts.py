"""Read routes for the overview and a service's page: hosts and the services on them."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from bleep.api.deps import Watching, get_parts, get_watching
from bleep.api.errors import Refusal
from bleep.hosts.config import UrlCheckConfig
from bleep.schemas.api import HostView, ServiceDetail, ServiceView, UrlHistory
from bleep.watcher.overview import Overview
from bleep.wiring import AppParts

router = APIRouter(tags=["hosts"])


def _overview(parts: AppParts, watching: Watching | None) -> Overview:
    return Overview(
        parts.registry,
        parts.incidents,
        parts.quiet,
        parts.url_checks,
        watching=watching,
        sightings=parts.sightings,
        health=parts.service_health,
    )


@router.get("/hosts")
async def list_hosts(
    parts: Annotated[AppParts, Depends(get_parts)],
    watching: Annotated[Watching | None, Depends(get_watching)],
) -> list[HostView]:
    return await _overview(parts, watching).hosts()


@router.get("/services")
async def list_services(
    parts: Annotated[AppParts, Depends(get_parts)],
    watching: Annotated[Watching | None, Depends(get_watching)],
    host_id: str | None = None,
) -> list[ServiceView]:
    return await _overview(parts, watching).services(host_id)


@router.get("/services/{service_id:path}/suggested-checks")
async def suggested_checks(
    service_id: str, parts: Annotated[AppParts, Depends(get_parts)]
) -> list[UrlCheckConfig]:
    """URL checks the switched-on plugins suggest for the service, such as its Coolify
    application's domains, without those it has (#348); empty when no plugin suggests any. They
    are offered on the service's page and saved like any URL set there; nothing is checked before.
    Declared before `GET /services/{id}`, which would otherwise take this path too."""
    service = parts.registry.service(service_id)
    if service is None:
        raise Refusal(404, "no_service")
    return await parts.plugins.suggested_checks(service)


@router.get("/services/{service_id:path}")
async def get_service(
    service_id: str,
    parts: Annotated[AppParts, Depends(get_parts)],
    watching: Annotated[Watching | None, Depends(get_watching)],
) -> ServiceDetail:
    """One service as the overview shows it, with its own rules and its host's, since when it
    has had its health and each configured URL's recent checks.

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
        raise Refusal(404, "no_service")
    view = await _overview(parts, watching).service(service, host)
    history = await parts.url_checks.history(service.host_id, service.id)
    return ServiceDetail(
        service=view,
        rules=list(service.rules),
        host_rules=list(host.config.rules),
        health_since=await _health_since(parts, view),
        recent_checks=[UrlHistory(url=u.url, checks=history.get(u.url, [])) for u in service.urls],
    )


async def _health_since(parts: AppParts, view: ServiceView) -> datetime | None:
    """When the service's current health began, as the watcher stored it. A stored health that
    differs from the one judged now (the watcher has not judged the change yet) gives no time."""
    stored = await parts.service_health.current(view.id)
    if stored is None or stored[0] != view.health:
        return None
    return stored[1]
