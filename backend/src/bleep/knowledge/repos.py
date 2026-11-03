"""The linked repos' docs for knowledge search, read through the GitHub client.

A service links a repo with `repo` in the hosts file, and names its docs with `docs_paths`
(folders or markdown files; `docs` when it names none). The links are read from the hosts file,
not from discovery, so a refresh that runs before the hosts are first polled still sees every
repo and does not drop its docs.
"""

import contextlib
from collections.abc import Iterable
from typing import Protocol

from bleep.hosts.github import GithubClient, NotFound
from bleep.knowledge.ingest import MAX_REPO_FILES
from bleep.schemas.github import RepoFile
from bleep.schemas.services import HostState

DEFAULT_DOCS = ("docs",)
MARKDOWN = (".md", ".markdown")


class HostsSource(Protocol):
    """What the adapter needs of the registry: the hosts file's view of every host."""

    def hosts(self) -> list[HostState]: ...


def _is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN)


class GithubRepoDocs:
    """`RepoDocs` over GitHub: each linked repo's README, then the markdown under its doc paths,
    at most `MAX_REPO_FILES` files. A missing README, folder or file is skipped; any other GitHub
    error (rate limit, outage) is raised, so the repo keeps the docs it had."""

    def __init__(self, client: GithubClient, registry: HostsSource) -> None:
        self.client = client
        self.registry = registry

    def _links(self) -> dict[str, list[str]]:
        """Each linked repo with its doc paths, in the order the hosts file names them."""
        links: dict[str, list[str]] = {}
        for host in self.registry.hosts():
            for service in host.config.services.values():
                if service.repo:
                    paths = links.setdefault(service.repo, [])
                    paths += [p for p in service.docs_paths or DEFAULT_DOCS if p not in paths]
        return links

    async def linked_repos(self) -> list[str]:
        return sorted(self._links())

    async def _markdown_under(self, repo: str, path: str) -> list[str]:
        if _is_markdown(path):
            return [path]
        try:
            entries = await self.client.list_files(repo, path, max_files=MAX_REPO_FILES)
        except NotFound:
            return []
        return [e.path for e in entries if _is_markdown(e.path)]

    async def _read(self, repo: str, paths: Iterable[str], room: int) -> list[RepoFile]:
        files: list[RepoFile] = []
        for path in paths:
            if len(files) >= room:
                break
            try:
                files.append(await self.client.read_file(repo, path))
            except NotFound:
                continue
        return files

    async def doc_files(self, repo: str) -> list[RepoFile]:
        files: list[RepoFile] = []
        with contextlib.suppress(NotFound):
            files.append(await self.client.readme(repo))
        wanted: list[str] = []
        for path in self._links().get(repo, list(DEFAULT_DOCS)):
            wanted += [p for p in await self._markdown_under(repo, path) if p not in wanted]
        seen = {f.path for f in files}
        files += await self._read(
            repo, [p for p in wanted if p not in seen], MAX_REPO_FILES - len(files)
        )
        return files
