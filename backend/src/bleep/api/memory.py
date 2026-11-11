"""A service's facts in long-term memory: listed, added by a person, and deleted.

A service id holds a `/` (`<host>/<key>`), so these routes take it as a path and are registered
before `GET /services/{id}` (`main.create_app`), which would otherwise take `.../facts` too.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from bleep.api.deps import get_parts
from bleep.api.errors import Refusal
from bleep.schemas.api import FactBody
from bleep.schemas.graph import ServiceFact
from bleep.wiring import AppParts

router = APIRouter(tags=["memory"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/services/{service_id:path}/facts")
async def list_facts(service_id: str, parts: Parts) -> list[ServiceFact]:
    """Oldest first; a person's and those learned from reports alike."""
    return await parts.memory.facts(service_id)


@router.post("/services/{service_id:path}/facts")
async def add_fact(service_id: str, body: FactBody, parts: Parts) -> ServiceFact:
    """Keep a fact a person wrote; the next incident of the service gets it from Gather."""
    if parts.registry.service(service_id) is None:
        raise Refusal(404, "no_service")
    return await parts.memory.add_fact(service_id, body.text, source="human")


@router.delete("/services/{service_id:path}/facts/{fact_id}", status_code=204)
async def delete_fact(service_id: str, fact_id: int, parts: Parts) -> Response:
    """Forget one of the service's facts, a person's or a report's."""
    if not await parts.memory.delete_fact(fact_id, service_id=service_id):
        raise Refusal(404, "no_fact")
    return Response(status_code=204)
