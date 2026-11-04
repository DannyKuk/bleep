"""What knowledge search keeps and finds: documents from three sources and the hits a search
returns."""

from typing import Literal

from pydantic import BaseModel, Field

KnowledgeSource = Literal["docker", "repo", "incident"]
KNOWLEDGE_SOURCES: tuple[KnowledgeSource, ...] = ("docker", "repo", "incident")


class KnowledgeDocument(BaseModel):
    """One page to index. `id` is stable across refreshes ("docker:volumes",
    "repo:owner/name:docs/setup.md", "incident:inc_…"), so a changed page replaces itself."""

    id: str = Field(min_length=1)
    source: KnowledgeSource
    title: str
    url: str
    text: str


class KnowledgeHit(BaseModel):
    """The closest chunk of one document."""

    source: KnowledgeSource
    title: str
    url: str
    heading: str  # the headings above the chunk, "A > B"; empty above the first heading
    text: str
    score: float
