"""Spike #338: Bleep's GitHub read tools, their failures and the repo docs ingest on github.com.

Runs inside the backend container, which has `GITHUB_TOKEN`, the hosts file and the local
embedding model, and calls no model:

    docker exec -i bleep-backend-1 /app/.venv/bin/python - < docs/spikes/github-live/spike.py

It builds the tools as `wiring` does (`github_specs` over a `GithubClient` whose token comes from
`wiring.github_token_source`), calls them on the service in the hosts file that links a repo,
and prints one JSON document: each call's latency, output size and shape checks, the failures in
Bleep's words, the rate-limit headers, and a knowledge search over the repo docs the running
backend ingested plus a one-off ingest (README and docs/) of a public repo into a throwaway
database. The raw GitHub answers are saved under /tmp/spike338 in the container for comparing
with the test fixtures. The token is never printed: the output only says how often it appeared.
"""

import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from bleep.config import Settings
from bleep.guard.sanitize import MAX_CHARS
from bleep.hosts.config import load_hosts_file
from bleep.hosts.github import API, API_VERSION, GithubClient
from bleep.knowledge.index import KnowledgeIndex
from bleep.knowledge.ingest import KnowledgeRefresher
from bleep.knowledge.repos import GithubRepoDocs
from bleep.store.db import Database
from bleep.tools.github import github_specs
from bleep.tools.knowledge import knowledge_specs
from bleep.tools.registry import ToolContext
from bleep.wiring import GITHUB_TOKEN_ENV, build_embedder, github_token_source

RAW = Path("/tmp/spike338")
PUBLIC_REPO = os.environ.get("SPIKE_PUBLIC_REPO", "DannyKuk/molecula")
MISSING_REPO = "DannyKuk/no-such-repo-bleep-338"
# Commits whose diffs carry the guard tests' fake secrets and wrapper tags.
GUARD_SHAS = os.environ.get("SPIKE_GUARD_SHAS", "c0443b6 f29f036 10977a2 7b559eb").split()
BOGUS_TOKEN = "github_pat_" + "0" * 82
SINCE_HOURS = 48
PATH_HOURS = 720
WRAPPED = re.compile(
    r'^<untrusted source="(\w+)" id="([0-9a-f]+)">\n.*\n</untrusted id="\2">$', re.S
)


class EnvSecrets:
    """What `github_token_source` reads of the secret store: `GITHUB_TOKEN` from the env, which
    `/api/secrets` reports as the source here. The live store's file is left alone."""

    def get(self, name: str) -> SecretStr | None:
        value = os.environ.get(name, "")
        return SecretStr(value) if value else None


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, incident_id: str, kind: str, detail: str) -> None:
        self.events.append((kind, detail))


class OneService:
    def __init__(self, repo: str | None) -> None:
        self.repo = repo

    def service(self, service_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=service_id, repo=self.repo)


OUTPUTS: list[str] = []


def shape(text: str) -> dict:
    match = WRAPPED.match(text)
    return {
        "chars": len(text),
        "within_cap": len(text) <= MAX_CHARS,
        "wrapped": bool(match),
        "source": match.group(1) if match else None,
        "cut_note": "[diff cut here" in text or "more commits not shown" in text,
        "redacted": text.count("<redacted"),
        "escaped_tags": text.count("&lt;"),
    }


async def timed(coro) -> tuple[object, float]:
    start = time.perf_counter()
    result = await coro
    return result, round((time.perf_counter() - start) * 1000, 1)


def tools_for(client: GithubClient, repo: str | None, recorder: Recorder) -> dict:
    ctx = ToolContext(
        incident_id="spike-338",
        host_id="spike",
        registry=OneService(repo),  # type: ignore[arg-type]
        guard_log=recorder,  # type: ignore[arg-type]
        service_id="spike/compose:shop",
    )
    return {spec.name: spec.build(ctx) for spec in github_specs(client)}


async def call(tools: dict, name: str, args: dict) -> dict:
    text, ms = await timed(tools[name].ainvoke(args))
    OUTPUTS.append(text)
    return {"args": args, "ms": ms, **shape(text), "head": text[:400], "tail": text[-300:]}


def ratelimit(headers: httpx.Headers) -> dict:
    return {k: headers.get(k) for k in headers if k.startswith("x-ratelimit") or k == "retry-after"}


async def raw_get(path: str, token: SecretStr | None, params: dict | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=10) as http:
        start = time.perf_counter()
        response = await http.get(path, params=params)
        ms = round((time.perf_counter() - start) * 1000, 1)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return {"status": response.status_code, "ms": ms, "ratelimit": ratelimit(response.headers),
            "body": body}  # fmt: skip


def save(name: str, data: object) -> None:
    RAW.mkdir(exist_ok=True)
    (RAW / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


async def github_part(results: dict) -> None:
    hosts = load_hosts_file(Path(os.environ["BLEEP_HOSTS_FILE"]))
    linked = [
        (h.id, key, s) for h in hosts.hosts for key, s in h.services.items() if s.repo is not None
    ]
    host_id, key, service = linked[0]
    repo = service.repo
    source = github_token_source(hosts.github, EnvSecrets())  # type: ignore[arg-type]
    client = GithubClient(source)
    token = source() if callable(source) else source
    results["step1"] = {
        "hosts_file_github_section": hosts.github is not None,
        "service": f"{host_id}: {key}",
        "repo": repo,
        "docs_paths": service.docs_paths,
        "readme": service.readme,
        "client_authenticated": client.authenticated,
        "token_env": GITHUB_TOKEN_ENV,
    }

    recorder = Recorder()
    tools = tools_for(client, repo, recorder)
    step2: dict = {"tool_calls": {}}
    step2["tool_calls"]["recent_commits_default"] = await call(tools, "recent_commits", {})
    step2["tool_calls"]["recent_commits_since"] = await call(
        tools, "recent_commits", {"since_hours": SINCE_HOURS}
    )
    step2["tool_calls"]["recent_commits_path"] = await call(
        tools, "recent_commits", {"since_hours": PATH_HOURS, "path": "demo/shop"}
    )
    step2["tool_calls"]["recent_commits_file"] = await call(
        tools, "recent_commits", {"since_hours": PATH_HOURS, "path": "demo/compose.yaml"}
    )

    since = datetime.now(UTC) - timedelta(hours=SINCE_HOURS)
    listed, ms = await timed(client.commits(repo, since, limit=20))
    step2["client_commits"] = {"ms": ms, "count": len(listed)}
    raw_list = await raw_get(
        f"/repos/{repo}/commits",
        token,
        {"since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": "20"},
    )
    save("commits", raw_list["body"])
    step2["raw_commits"] = {k: raw_list[k] for k in ("status", "ms", "ratelimit")}
    raw_path = await raw_get(
        f"/repos/{repo}/commits",
        token,
        {"since": (datetime.now(UTC) - timedelta(hours=PATH_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "per_page": "20", "path": "demo/shop"},
    )  # fmt: skip
    save("commits-path", raw_path["body"])

    newest = listed[0].sha
    step2["tool_calls"]["commit_diff_full_sha"] = await call(tools, "commit_diff", {"sha": newest})
    step2["tool_calls"]["commit_diff_prefix"] = await call(
        tools, "commit_diff", {"sha": newest[:7]}
    )
    step2["tool_calls"]["commit_diff_upper_prefix"] = await call(
        tools, "commit_diff", {"sha": newest[:10].upper()}
    )
    raw_commit = await raw_get(f"/repos/{repo}/commits/{newest}", token)
    save("commit", raw_commit["body"])
    step2["raw_commit"] = {k: raw_commit[k] for k in ("status", "ms")}
    _, ms = await timed(client.commit(repo, newest[:7]))
    step2["client_commit_prefix_ms"] = ms

    for sha in GUARD_SHAS:
        before = len(recorder.events)
        step2["tool_calls"][f"commit_diff_guard_{sha}"] = {
            **await call(tools, "commit_diff", {"sha": sha}),
            "guard_log": recorder.events[before:],
        }
        raw = await raw_get(f"/repos/{repo}/commits/{sha}", token)
        save(f"commit-{sha}", raw["body"])
    results["step2"] = step2

    step3: dict = {}
    bogus = GithubClient(SecretStr(BOGUS_TOKEN))
    step3["wrong_token_tool"] = await call(tools_for(bogus, repo, Recorder()), "recent_commits", {})
    raw_401 = await raw_get(f"/repos/{repo}/commits", SecretStr(BOGUS_TOKEN))
    step3["wrong_token_raw"] = raw_401
    step3["missing_repo_tool"] = await call(
        tools_for(client, MISSING_REPO, Recorder()), "recent_commits", {}
    )
    step3["missing_repo_diff_tool"] = await call(
        tools_for(client, MISSING_REPO, Recorder()), "commit_diff", {"sha": newest}
    )
    step3["missing_repo_raw"] = await raw_get(f"/repos/{MISSING_REPO}/commits", token)
    step3["unknown_sha_tool"] = await call(tools, "commit_diff", {"sha": "0000000"})
    step3["unknown_full_sha_tool"] = await call(tools, "commit_diff", {"sha": "a" * 40})
    step3["unknown_sha_raw"] = await raw_get(f"/repos/{repo}/commits/0000000", token)
    step3["private_repo_without_token_tool"] = await call(
        tools_for(GithubClient(None), repo, Recorder()), "recent_commits", {}
    )
    step3["rate_limit_token"] = await raw_get("/rate_limit", token)
    step3["rate_limit_no_token"] = await raw_get("/rate_limit", None)
    for name in ("rate_limit_token", "rate_limit_no_token"):
        body = step3[name]["body"]
        step3[name]["body"] = {k: body["resources"][k] for k in ("core", "search")}
    results["step3"] = step3
    results["_client"] = client
    results["_token"] = token


async def knowledge_part(results: dict, client: GithubClient) -> None:
    settings = Settings()
    embedder = build_embedder(settings)
    step4: dict = {"embedder": embedder.name}

    live = KnowledgeIndex(Database(Path("/data/bleep.db")), embedder)
    step4["live_repo_documents"] = sorted(await live.document_ids("repo:"))
    step4["live_refresh"] = await live.last_refresh("repos")
    ctx = ToolContext(incident_id="spike-338", host_id="spike", registry=None)  # type: ignore[arg-type]
    search = knowledge_specs(live)[0].build(ctx)
    for query in (
        "how is the shop deployed",
        "which services does the shop run and what does the worker do",
    ):
        text, ms = await timed(search.ainvoke({"query": query, "sources": ["repo"]}))
        OUTPUTS.append(text)
        step4.setdefault("live_search", []).append(
            {"query": query, "ms": ms, **shape(text), "head": text[:700]}
        )
    text, ms = await timed(search.ainvoke({"query": "how is the shop deployed"}))
    step4["live_search_all_sources"] = {"ms": ms, **shape(text), "head": text[:900]}

    db = Database(RAW / "knowledge.db")
    if db.path.exists():
        db.path.unlink()
    await db.migrate()
    scratch = KnowledgeIndex(db, embedder)
    readme_hosts = SimpleNamespace(
        hosts=lambda: [
            SimpleNamespace(
                config=SimpleNamespace(
                    services={"app": SimpleNamespace(repo=PUBLIC_REPO, docs_paths=[], readme=True)}
                )
            )
        ]
    )
    refresher = KnowledgeRefresher(
        scratch, snapshot_dir=None, repos=GithubRepoDocs(client, readme_hosts)
    )
    refreshed, ms = await timed(refresher.refresh_repos())
    step4["public_repo"] = {
        "repo": PUBLIC_REPO,
        "ms": ms,
        "refresh": vars(refreshed),
        "documents": sorted(await scratch.document_ids("repo:")),
    }
    search = knowledge_specs(scratch)[0].build(ctx)
    for query in ("how do I set up molecula locally", "what is the architecture of molecula"):
        text, ms = await timed(search.ainvoke({"query": query, "sources": ["repo"]}))
        OUTPUTS.append(text)
        step4.setdefault("public_search", []).append(
            {"query": query, "ms": ms, **shape(text), "head": text[:700]}
        )
    results["step4"] = step4


async def main() -> None:
    results: dict = {"at": datetime.now(UTC).isoformat()}
    await github_part(results)
    client, token = results.pop("_client"), results.pop("_token")
    await knowledge_part(results, client)
    secret = token.get_secret_value() if token is not None else None
    dumps = "".join(p.read_text() for p in RAW.glob("*.json"))
    results["token_seen"] = {
        "in_tool_outputs": sum(o.count(secret) for o in OUTPUTS) if secret else None,
        "in_raw_dumps": dumps.count(secret) if secret else None,
        "in_this_report": json.dumps(results, default=str).count(secret) if secret else None,
    }
    print(json.dumps(results, indent=1, default=str))


asyncio.run(main())
