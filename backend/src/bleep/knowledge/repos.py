"""The linked repos' docs for knowledge search, read through the GitHub client.

A service links a repo with `repo` in the hosts file, and names its docs with `docs_paths`
(folders or markdown files; `docs` when it names none); `readme: false` leaves the repo's README
out when no service linking the repo wants it. The links are read from the hosts file,
not from discovery, so a refresh that runs before the hosts are first polled still sees every
repo and does not drop its docs.
"""

import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from bleep.hosts.github import (
    GithubClient,
    GithubError,
    GithubUnavailable,
    NotFound,
    RateLimited,
    TooLarge,
)
from bleep.knowledge.ingest import MAX_REPO_FILES, RepoDocFiles
from bleep.schemas.github import RepoFile
from bleep.schemas.services import HostState

log = logging.getLogger(__name__)

DEFAULT_DOCS = ("docs",)
MARKDOWN = (".md", ".markdown")


class HostsSource(Protocol):
    """What the adapter needs of the registry: the hosts file's view of every host."""

    def hosts(self) -> list[HostState]: ...


def _is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN)


@dataclass
class _Link:
    paths: list[str] = field(default_factory=list)
    readme: bool = False


class GithubRepoDocs:
    """`RepoDocs` over GitHub: each linked repo's README, then the markdown under its doc paths,
    at most `MAX_REPO_FILES` files. A missing README or folder is skipped, and so is a doc path
    whose last part has an extension other than markdown (a compose file, say) and a file that is
    gone or too large to read. A rate limit or an outage is raised, so the repo keeps the docs it
    had; a file that fails to read for any other reason is reported unread and keeps its own."""

    def __init__(self, client: GithubClient, registry: HostsSource) -> None:
        self.client = client
        self.registry = registry

    def _links(self) -> dict[str, _Link]:
        """Each linked repo with its doc paths, in the order the hosts file names them, and
        whether any service linking it wants its README."""
        links: dict[str, _Link] = {}
        for host in self.registry.hosts():
            for service in host.config.services.values():
                if service.repo:
                    link = links.setdefault(service.repo, _Link())
                    link.paths += [
                        p for p in service.docs_paths or DEFAULT_DOCS if p not in link.paths
                    ]
                    link.readme = link.readme or service.readme
        return links

    async def linked_repos(self) -> list[str]:
        return sorted(self._links())

    async def _markdown_under(self, repo: str, path: str) -> list[str]:
        if _is_markdown(path):
            return [path]
        if "." in path.rsplit("/", 1)[-1]:  # another kind of file (compose.yaml): not a doc
            return []
        try:
            entries = await self.client.list_files(
                repo, path, max_files=MAX_REPO_FILES, suffixes=MARKDOWN
            )
        except NotFound:
            return []
        return [e.path for e in entries]

    async def _read(self, repo: str, paths: Iterable[str], room: int) -> RepoDocFiles:
        files: list[RepoFile] = []
        unread: list[str] = []
        for path in paths:
            if len(files) + len(unread) >= room:
                break
            try:
                files.append(await self.client.read_file(repo, path))
            except RateLimited, GithubUnavailable:
                raise
            except (NotFound, TooLarge) as error:  # its doc goes: GitHub no longer serves it
                log.info("knowledge: skipped %s in %s (%s)", path, repo, error)
            except GithubError as error:
                log.warning("knowledge: could not read %s in %s, kept (%s)", path, repo, error)
                unread.append(path)
        return RepoDocFiles(files=files, unread=unread)

    async def doc_files(self, repo: str) -> RepoDocFiles:
        link = self._links().get(repo, _Link(list(DEFAULT_DOCS), readme=True))
        files: list[RepoFile] = []
        if link.readme:
            with contextlib.suppress(NotFound):
                files.append(await self.client.readme(repo))
        wanted: list[str] = []
        for path in link.paths:
            wanted += [p for p in await self._markdown_under(repo, path) if p not in wanted]
        seen = {f.path for f in files}
        docs = await self._read(
            repo, [p for p in wanted if p not in seen], MAX_REPO_FILES - len(files)
        )
        return RepoDocFiles(files=files + docs.files, unread=docs.unread)
