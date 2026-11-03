"""Refresh Bleep's curated snapshot of the Docker documentation (backend/knowledge/docker-docs/).

Each page in `PAGES` is read from the docker/docs repository (Apache-2.0) at one commit and
written as markdown with front matter: title, source_url, source_path, commit, fetched, license.
Hugo shortcodes are resolved or dropped, links keep only their text, and the long reference pages
keep only the sections listed for them. The CLI reference pages are built from their YAML data
(description, options, examples). The repository's licence is copied next to the pages.

Run from anywhere: uv run --project backend python scripts/fetch_docker_docs.py [--ref main]
Then re-run the retrieval check (backend/evals/knowledge.py) and update its dated report.
"""

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "knowledge" / "docker-docs"
REPO = "docker/docs"
LICENSE = "Apache-2.0"
SITE = "https://docs.docker.com"
TIMEOUT_S = 30.0

Read = Callable[[str], str]  # a path in the repository (or an include name) to its text


@dataclass(frozen=True)
class Page:
    slug: str
    path: str  # in the docker/docs repository
    url: str  # where docs.docker.com publishes it, relative to SITE
    sections: tuple[str, ...] = field(default=())  # headings to keep; empty keeps the whole page


PAGES: tuple[Page, ...] = (
    Page(
        "restart-policies",
        "content/manuals/engine/containers/start-containers-automatically.md",
        "/engine/containers/start-containers-automatically/",
    ),
    Page(
        "resource-constraints",
        "content/manuals/engine/containers/resource_constraints.md",
        "/engine/containers/resource_constraints/",
    ),
    Page(
        "runtime-metrics",
        "content/manuals/engine/containers/runmetrics.md",
        "/engine/containers/runmetrics/",
    ),
    Page(
        "running-containers",
        "_vendor/github.com/docker/cli/docs/reference/run.md",
        "/engine/containers/run/",
        ("Exit status", "Runtime constraints on resources", "Healthchecks"),
    ),
    Page(
        "logging-configure",
        "content/manuals/engine/logging/configure.md",
        "/engine/logging/configure/",
    ),
    Page(
        "logging-json-file",
        "content/manuals/engine/logging/drivers/json-file.md",
        "/engine/logging/drivers/json-file/",
    ),
    Page(
        "logging-local",
        "content/manuals/engine/logging/drivers/local.md",
        "/engine/logging/drivers/local/",
    ),
    Page(
        "logging-dual",
        "content/manuals/engine/logging/dual-logging.md",
        "/engine/logging/dual-logging/",
    ),
    Page("daemon-logs", "content/manuals/engine/daemon/logs.md", "/engine/daemon/logs/"),
    Page(
        "daemon-troubleshoot",
        "content/manuals/engine/daemon/troubleshoot.md",
        "/engine/daemon/troubleshoot/",
    ),
    Page(
        "live-restore",
        "content/manuals/engine/daemon/live-restore.md",
        "/engine/daemon/live-restore/",
    ),
    Page("networking", "content/manuals/engine/network/_index.md", "/engine/network/"),
    Page(
        "network-bridge",
        "content/manuals/engine/network/drivers/bridge.md",
        "/engine/network/drivers/bridge/",
    ),
    Page(
        "port-publishing",
        "content/manuals/engine/network/port-publishing.md",
        "/engine/network/port-publishing/",
    ),
    Page(
        "volumes",
        "content/manuals/engine/storage/volumes.md",
        "/engine/storage/volumes/",
    ),
    Page("tmpfs", "content/manuals/engine/storage/tmpfs.md", "/engine/storage/tmpfs/"),
    Page(
        "bind-mounts",
        "content/manuals/engine/storage/bind-mounts.md",
        "/engine/storage/bind-mounts/",
    ),
    Page(
        "pruning",
        "content/manuals/engine/manage-resources/pruning.md",
        "/engine/manage-resources/pruning/",
    ),
    Page(
        "compose-startup-order",
        "content/manuals/compose/how-tos/startup-order.md",
        "/compose/how-tos/startup-order/",
    ),
    Page(
        "compose-networking",
        "content/manuals/compose/how-tos/networking.md",
        "/compose/how-tos/networking/",
    ),
    Page(
        "compose-environment-variables",
        "content/manuals/compose/how-tos/environment-variables/set-environment-variables.md",
        "/compose/how-tos/environment-variables/set-environment-variables/",
    ),
    Page(
        "compose-services",
        "content/reference/compose-file/services.md",
        "/reference/compose-file/services/",
        (
            "depends_on",
            "env_file",
            "environment",
            "healthcheck",
            "logging",
            "mem_limit",
            "mem_reservation",
            "memswap_limit",
            "network_mode",
            "oom_kill_disable",
            "oom_score_adj",
            "pids_limit",
            "ports",
            "restart",
            "stop_grace_period",
            "stop_signal",
            "tmpfs",
            "volumes",
        ),
    ),
    Page(
        "compose-deploy",
        "content/reference/compose-file/deploy.md",
        "/reference/compose-file/deploy/",
    ),
    Page(
        "dockerfile-healthcheck",
        "_vendor/github.com/moby/buildkit/frontend/dockerfile/docs/reference.md",
        "/reference/dockerfile/",
        ("STOPSIGNAL", "HEALTHCHECK"),
    ),
    Page(
        "cli-events",
        "data/cli/engine/docker_system_events.yaml",
        "/reference/cli/docker/system/events/",
    ),
    Page(
        "cli-logs",
        "data/cli/engine/docker_container_logs.yaml",
        "/reference/cli/docker/container/logs/",
    ),
    Page(
        "cli-system-df",
        "data/cli/engine/docker_system_df.yaml",
        "/reference/cli/docker/system/df/",
    ),
)

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_INCLUDE = re.compile(r'\{\{[<%]\s*include\s+"([^"]+)"\s*[>%]\}\}')
_TAB = re.compile(r'\{\{<\s*tab\s+name="([^"]+)"\s*>\}\}')
_SHORTCODE = re.compile(r"\{\{[<%].*?[>%]\}\}")
_LINK = re.compile(r"(!?)\[([^\]]*)\]\([^)]*\)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_ANCHOR = re.compile(r"\s*\{#[^}]*\}\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


class FetchError(Exception):
    """A page could not be read from the repository."""


def split_front_matter(text: str) -> tuple[dict, str]:
    """The page's YAML front matter and the body after it."""
    match = _FRONT_MATTER.match(text)
    if match is None:
        return {}, text
    return yaml.safe_load(match.group(1)) or {}, text[match.end() :]


def clean_hugo(text: str, include: Read) -> str:
    """Markdown without Hugo: includes inlined, tab names kept as bold lines, other shortcodes,
    HTML comments and heading anchors dropped, links and images reduced to their text."""
    text = _INCLUDE.sub(lambda m: clean_hugo(include(m.group(1)), include), text)
    text = _TAB.sub(lambda m: f"**{m.group(1)}**", text)
    text = _SHORTCODE.sub("", _COMMENT.sub("", text))
    text = _LINK.sub(lambda m: "" if m.group(1) else m.group(2), text)
    lines = [
        _HEADING_ANCHOR.sub("", line) if _HEADING.match(line) else line for line in text.split("\n")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def _heading_name(text: str) -> str:
    return text.replace("`", "").strip()


def select_sections(text: str, names: tuple[str, ...]) -> str:
    """Only the sections whose heading is one of `names` (backticks ignored), each with its
    subsections. Headings inside fenced code are not headings."""
    if not names:
        return text
    wanted = set(names)
    kept: list[str] = []
    level = 0  # the level of the section being kept, 0 when none
    in_fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            depth = len(heading.group(1))
            if level and depth <= level:
                level = 0
            if not level and _heading_name(heading.group(2)) in wanted:
                level = depth
        if level:
            kept.append(line)
    return "\n".join(kept)


def cli_page(data: dict) -> tuple[str, str]:
    """The title and markdown of a CLI reference page from its YAML data."""
    command = data["command"]
    parts = [data.get("short", "").strip()]
    if data.get("usage"):
        parts.append(f"Usage: `{data['usage'].strip()}`")
    if data.get("aliases"):
        parts.append(f"Aliases: {data['aliases']}")
    if data.get("long"):
        parts.append(data["long"].strip())
    options = [o for o in data.get("options", []) if not o.get("hidden")]
    if options:
        rows = [
            f"- `--{o['option']}`"
            + (f" (`-{o['shorthand']}`)" if o.get("shorthand") else "")
            + f": {o.get('description', '').strip()}"
            + (f" Default: `{o['default_value']}`." if o.get("default_value") else "")
            for o in options
        ]
        parts.append("## Options\n\n" + "\n".join(rows))
    if data.get("examples"):
        parts.append("## Examples\n\n" + data["examples"].strip())
    return command, "\n\n".join(p for p in parts if p)


def render(page: Page, title: str, body: str, commit: str, fetched: str) -> str:
    """The page as Bleep ships it: front matter, then the title as the top heading."""
    front = {
        "title": title,
        "source_url": SITE + page.url,
        "source_path": page.path,
        "commit": commit,
        "fetched": fetched,
        "license": LICENSE,
    }
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{head}\n---\n\n# {title}\n\n{body.strip()}\n"


class Include:
    """Reads `content/includes/<name>` at the snapshot's commit."""

    def __init__(self, read: Read) -> None:
        self.read = read

    def __call__(self, name: str) -> str:
        return split_front_matter(self.read(f"content/includes/{name}"))[1]


def build_page(page: Page, read: Read, commit: str, fetched: str) -> str:
    raw = read(page.path)
    if page.path.endswith(".yaml"):
        title, body = cli_page(yaml.safe_load(raw))
    else:
        meta, body = split_front_matter(raw)
        title = str(meta.get("title") or page.slug)
        body = select_sections(clean_hugo(body, Include(read)), page.sections)
    if not body.strip():
        raise FetchError(f"{page.path}: nothing left after selecting {page.sections}")
    return render(page, title, body, commit, fetched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="main", help="branch, tag or commit of docker/docs")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as http:
        reply = http.get(f"https://api.github.com/repos/{REPO}/commits/{args.ref}")
        reply.raise_for_status()
        commit = reply.json()["sha"]

        def read(path: str) -> str:
            got = http.get(f"https://raw.githubusercontent.com/{REPO}/{commit}/{path}")
            if got.status_code != 200:
                raise FetchError(f"{path}: HTTP {got.status_code}")
            return got.text

        fetched = datetime.now(UTC).date().isoformat()
        args.out.mkdir(parents=True, exist_ok=True)
        written = set()
        for page in PAGES:
            (args.out / f"{page.slug}.md").write_text(
                build_page(page, read, commit, fetched), encoding="utf-8"
            )
            written.add(f"{page.slug}.md")
            print(f"{page.slug}: {page.path}")
        (args.out / "LICENSE").write_text(read("LICENSE"), encoding="utf-8")
    for stale in sorted(args.out.glob("*.md")):
        if stale.name not in written and stale.name != "README.md":
            stale.unlink()
            print(f"removed {stale.name}")
    print(f"{len(written)} pages from {REPO}@{commit[:12]} into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
