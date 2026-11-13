"""The linked repos' docs in knowledge search: what Bleep holds of them, and reading them now
(#556) instead of at the next daily refresh."""

from typing import Annotated

from fastapi import APIRouter, Depends

from bleep.api.deps import get_parts
from bleep.schemas.knowledge import RepoDocsState
from bleep.wiring import AppParts

router = APIRouter(tags=["knowledge"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/knowledge/repos")
async def repo_docs(parts: Parts) -> RepoDocsState:
    """When the repos' docs were last read, the chunks each linked repo has, whether a read is
    running and how the last one a person asked for ended."""
    return await parts.knowledge_refresher.repo_docs_state()


@router.post("/knowledge/repos/refresh", status_code=202)
async def refresh_repo_docs(parts: Parts) -> RepoDocsState:
    """Start reading every linked repo's docs now, in the background: a read can outlast a
    request. A read already running is joined, so a second press does not read GitHub twice. The
    page follows it with `GET /knowledge/repos` while `reading` is true."""
    parts.knowledge_refresher.start_repo_refresh()
    return await parts.knowledge_refresher.repo_docs_state()
