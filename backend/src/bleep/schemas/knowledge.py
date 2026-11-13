"""What knowledge search keeps and finds: documents from three sources and the hits a search
returns."""

from datetime import datetime
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


class RepoDocsRepo(BaseModel):
    repo: str  # "owner/name"
    chunks: int  # what knowledge search holds of its docs; 0 until they are read


class RepoDocsResult(BaseModel):
    """How the last read ended, whoever started it: `read` of the `linked` repos could be read
    and `embedded` documents were new or changed; or `failed`, with the reason in Bleep's log."""

    failed: bool
    read: int
    linked: int
    embedded: int


class RepoDocsState(BaseModel):
    """What Bleep holds of the linked repos' docs. `last_read_at` is when a refresh last read
    the repos linked then; None before the first. `reading` is true while a refresh runs, and
    `result` is how the last one ended, None until one has."""

    last_read_at: datetime | None
    repos: list[RepoDocsRepo]
    reading: bool = False
    result: RepoDocsResult | None = None
