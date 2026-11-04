"""What Bleep reads from a service's GitHub repo. Every text field came from the repo, so it is
untrusted wherever it goes next."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Commit(BaseModel):
    sha: str
    date: datetime  # when it reached the branch (the committer's date)
    author: str  # the GitHub login, or the commit's author name when it has none
    message: str
    url: str | None = None


class FileChange(BaseModel):
    filename: str
    status: str  # added, modified, removed, renamed, ...
    additions: int = 0
    deletions: int = 0
    patch: str | None = None  # None for a binary file or one too large for GitHub to diff
    previous_filename: str | None = None


class CommitDetail(Commit):
    files: list[FileChange] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0


class RepoEntry(BaseModel):
    path: str
    type: Literal["file", "dir", "symlink", "submodule"]
    size: int = 0
    url: str | None = None


class RepoFile(BaseModel):
    path: str
    sha: str  # the git blob's sha: it changes exactly when the content does
    text: str
    url: str | None = None
