"""The linked repos' docs in knowledge search: what Bleep holds of them, and reading them now
(#556) instead of at the next daily refresh."""

from typing import Annotated

from fastapi import APIRouter, Depends

from bleep.api.deps import get_parts
from bleep.schemas.knowledge import RepoDocsRefreshed, RepoDocsState
from bleep.wiring import AppParts

router = APIRouter(tags=["knowledge"])

Parts = Annotated[AppParts, Depends(get_parts)]


@router.get("/knowledge/repos")
async def repo_docs(parts: Parts) -> RepoDocsState:
    """When the repos' docs were last read, and the chunks each linked repo has."""
    return await parts.knowledge_refresher.repo_docs_state()


@router.post("/knowledge/repos/refresh")
async def refresh_repo_docs(parts: Parts) -> RepoDocsRefreshed:
    """Read every linked repo's docs now. A refresh already running is joined, so a second press
    does not read GitHub twice."""
    return await parts.knowledge_refresher.refresh_repos_now()
