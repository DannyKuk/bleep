# Spike: the GitHub read tools and the repo docs ingest on github.com

Measured on 2026-11-09 with [`spike.py`](spike.py), run inside the live backend container (#338). Steps 1-4 of the issue are below; step 5, the live incident with a real model, is to come.

**Every number below comes from the one saved run of the script (09:19 UTC) or from the backend's own start (09:15 UTC).** Latencies are single calls, not a spread. No model was called in steps 1-4.

## Setup

| | |
|---|---|
| Bleep | `main` at `eb46b75`, the image built from it, Docker Desktop on an M-series Mac |
| Hosts file | the laptop's engine through the socket proxy (host `laptop`); the demo shop's service `shop` given `repo: DannyKuk/bleep`, `docs_paths: [demo/shop/docs]`, `readme: false`, as in [`config/hosts.demo.yaml`](../../../config/hosts.demo.yaml); no `github` section |
| Token | `GITHUB_TOKEN` in `.env`: read-only, every repo of the account |
| Repos | `DannyKuk/bleep` (private, real history: 11 commits in the 48 hours before the run) and `DannyKuk/molecula` (public, a README and 39 markdown files under `docs/`) |
| Embedder | `fastembed:BAAI/bge-small-en-v1.5`, local, on the CPU: knowledge search calls no API |
| Network | home connection to `api.github.com` |

The script builds the tools as `wiring` does: `github_specs` over a `GithubClient` whose token comes from `wiring.github_token_source`, read from the env as the secret store does. It reads the service from the hosts file and gives the tools a stand-in registry that returns it, since the tools only ask the registry for the service's `repo`. It also saves GitHub's raw answers to compare with the test fixtures.

```bash
docker compose up -d backend    # picks up GITHUB_TOKEN from .env and the hosts file
docker exec -i bleep-backend-1 /app/.venv/bin/python - < docs/spikes/github-live/spike.py > out.json
docker cp bleep-backend-1:/tmp/spike338 raw    # GitHub's raw answers
```

## Step 1: the token and the repo link

- **Before the restart `/api/secrets` said `GITHUB_TOKEN` was not set.** The container had started before the token was added to `.env`. The env is read only when the container starts ([settings](../../design/settings.md#secrets-from-the-dashboard-135), Limits), and `docker compose restart` keeps the old one: `up -d` creates the container again with the new `.env`.
- **After `docker compose up -d backend`: `set: true, source: "env", env_owned: true`.** The hosts file needs no `github` section: without one, `github_token_source` reads `GITHUB_TOKEN` from the env, or else from the dashboard. The client reported `authenticated`.
- **The shop has its repo.** The hosts file's service `shop` matched the discovered `laptop/compose:shop` by name. The API's service views have no `repo` field, so the link was checked from the hosts file in the container and through the tools, which are offered only for a service with a repo.
- **The repo docs were in knowledge search 3.8 s after the start.** The backend started at 09:15:32.4 UTC. The newly linked repo made the daily repo refresh due at once, and it recorded `repos` over `DannyKuk/bleep` at 09:15:36.2 UTC: `demo/shop/docs/deploy.md` (3 chunks) and `services.md` (5 chunks).

## Step 2: `recent_commits` and `commit_diff` on the real API

| Call | Latency | Output | Notes |
|---|---|---|---|
| `recent_commits()` (48 h) | 574 ms | 2,995 chars | 11 commits, each message cut at 600 chars with `[… cut]` |
| `recent_commits(since_hours=48)` | 447 ms | 2,995 chars | the same list |
| `recent_commits(since_hours=720, path="demo/shop")` | 475 ms | 2,085 chars | 6 commits, the newest `959048a` (#183) |
| `recent_commits(since_hours=720, path="demo/compose.yaml")` | 475 ms | 1,017 chars | a file path works as well as a folder |
| `commit_diff(sha=<40-char sha of eb46b75>)` | 539 ms | 4,420 chars | cut early; see below |
| `commit_diff(sha="eb46b75")` | 448 ms | 4,420 chars | same answer as the full sha |
| `commit_diff(sha="EB46B75D36")` | 450 ms | 4,420 chars | an upper-case prefix works too |
| `commit_diff(sha="c0443b6")` (#6, the guard) | 644 ms | 7,945 chars | 24 files, 185,229 chars of patches; 8 secrets redacted, 1 wrapper tag escaped |
| `commit_diff(sha="f29f036")` (#289) | 604 ms | 7,941 chars | 15 secrets redacted |
| `commit_diff(sha="10977a2")` (#255) | 657 ms | 7,911 chars | 19 secrets redacted |
| `commit_diff(sha="7b559eb")` (#312) | 518 ms | 7,934 chars | 8 wrapper look-alikes escaped (`&lt;`) |

- **The answers parse.** Every call returned Bleep's text. None hit "GitHub's answer has an unexpected shape".
- **The diffs are capped and guarded.** Every output was at most 8,000 chars and wrapped once (`<untrusted source="commits"…>` or `source="diff"`, closing tag with the same id). Each cut ended with Bleep's note naming the files not shown in full. The guard tests' fake keys in the three guard commits were redacted, and each redaction was recorded in the guard log (`N secret(s) redacted from diff output`). The escaped tags came from wrapper tags in the guard's own code and tests.
- **The token never showed.** It appeared 0 times in the tool outputs, the saved raw answers, the script's report, `docker logs bleep-backend-1`, the socket proxy's logs and `/data/bleep.db`. All of these were counted without printing it.
- **Latency: 450-660 ms for a call, about 425 ms of it GitHub's.** The raw request for the same list took 423 ms and for one commit 425 ms, so most of a call is GitHub's answer.
- **One diff came out half empty (#344).** `eb46b75` changes 8 files. The patches go in GitHub's file order, and the second file, an eval `.jsonl` with lines of several thousand chars, did not fit. `_shrink` cut it to its last line break, which left only its `@@` header, and the diff ended there. The last file, the 207-line eval report, was never shown, though about 3,500 chars of the budget were left.
- **An empty file is called "binary or too large" (#345).** GitHub sends no `patch` for an empty file. `c0443b6` adds two empty `__init__.py` files, and the diff described each as "(no text diff: binary or too large)".

### Shapes against the fixtures

The test fixtures (`backend/tests/fixtures/github/`, recorded from `encode/httpx` and trimmed) hold the right shapes. Every field Bleep reads is in the real answers with the same type, and no fixture field is missing from them. The real answers add fields Bleep ignores: `node_id`, `url`, `comments_url`, `parents`, `committer` (the GitHub user), `commit.tree`, `commit.verification`, `commit.comment_count`, the authors' emails, and for each file `sha`, `blob_url`, `raw_url` and `contents_url`. Other details of this repo's answers:

- `author` (the GitHub user) was set on all 11 commits. The committer was `GitHub` on all of them (squash merges), with the same date as the author. Bleep shows the committer's date.
- `stats` came with every commit (`total`, `additions`, `deletions`). Files had the statuses `added` and `modified`. No commit had more than 24 files, so GitHub's 300-file page limit for one commit was not reached.
- The commit list has no `files`, as the fixtures assume.

The eval fixture client (`backend/evals/github.py`) answers an unknown sha with 422, as GitHub does. Its commits have the same author and committer, where real squash merges have `GitHub` as committer; Bleep reads only the committer's date, so this changes nothing it shows.

What was added: the recorded 401 body (`unauthorized.json`) and a client test that a refused token is a plain `GithubError` in Bleep's words. The fixtures' README now says what was checked.

## Step 3: failures in Bleep's words

| Case | GitHub answered | The tool said | Latency |
|---|---|---|---|
| Made-up token | 401 `Bad credentials`, no `x-ratelimit-*` headers | "GitHub error: GitHub refused the token (401)." | 164 ms |
| Repo that does not exist (`DannyKuk/no-such-repo-bleep-338`), with the token | 404 `Not Found` | "GitHub has no repo DannyKuk/no-such-repo-bleep-338, or the token cannot read it." (both tools) | 342 / 293 ms |
| Unknown sha `0000000` | 422 `No commit found for SHA: 0000000` | "GitHub has no commit 0000000 in DannyKuk/bleep." | 391 ms |
| Unknown 40-char sha | 422 | "GitHub has no commit aaaa… in DannyKuk/bleep." | 356 ms |
| The private repo without a token | 404 | "GitHub has no repo DannyKuk/bleep, or it is private and no GitHub token (GITHUB_TOKEN) is set." | 260 ms |

- **GitHub's own messages never reached the model.** "Bad credentials" and "No commit found for SHA" stayed out of every answer. The failures come back as plain sentences, unwrapped, since they hold nothing from GitHub.
- **The 401 sentence is the weak one (#346).** It names GitHub twice and does not name `GITHUB_TOKEN`. Unlike the rate-limit sentence, it does not tell the model to go on without the repo.
- **The rate limit was far off.** With the token: 5,000 an hour (`x-ratelimit-resource: core`), 53 used at the end of step 3, most of them by this run. Without a token, from this machine: 60 an hour, 1 used. Every answer with the token carried `x-ratelimit-limit`, `-remaining`, `-reset`, `-used` and `-resource`. The 401 carried none. `RateLimited` was not triggered live; spending the limit would have taken the hour.

## Step 4: knowledge ingest and search

- **The linked repo's `docs_paths`.** The running backend's own refresher read `demo/shop/docs` of the private repo at start (step 1). Searching with `knowledge_search` (`sources: ["repo"]`) through the tool on the live index:
  - "how is the shop deployed": first hit `DannyKuk/bleep: demo/shop/docs/deploy.md > Deploying the shop`, score 0.77, 150 ms (the first search in the process);
  - "which services does the shop run and what does the worker do": first hit `services.md > The shop's services`, score 0.75, 9 ms;
  - with no `sources`, the deploy question still put both shop pages first, ahead of the Docker docs, in 12 ms.
- **A README.** A one-off `KnowledgeRefresher.refresh_repos` with `GithubRepoDocs` over a stand-in hosts list linked `DannyKuk/molecula` with `readme: true` and the default `docs`, into a throwaway database in the container. The live index was left alone. It read 1 repo and embedded 40 documents: the README and 39 of the markdown files under `docs/`. There the 40-file cap stopped it, and the client logged the warning it should. It took 73.7 s: 16.1 s of GitHub requests (one list per folder and one read per file, one at a time), about 58 s embedding 527,000 chars on the CPU. Searches on it: "how do I set up molecula locally" gave `docs/setup.md > Setup > Without Docker` (0.79, 11 ms), and "what is the architecture of molecula" gave `docs/architecture.md` (0.82, 10 ms).
- **Every search result was wrapped once** (`source="knowledge"`), each hit with its page title, headings and GitHub link.
- **The search embeds locally** (`EMBEDDINGS_PROVIDER` is unset, so `local`), so step 4 cost nothing.

## Step 5: a live incident whose cause is a recent commit

To come.

## Limits

- One run, from one machine, at one time of day; the latencies are single calls.
- The repos were the account's own. A repo the token cannot see was simulated by one that does not exist: GitHub answers both with 404, and the tool's sentence covers both.
- Secondary rate limits and a spent primary limit were not triggered.
- The whole run was a script inside the container, not the dashboard: the dev panel has no route that calls a tool. The tools ran on a stand-in registry and incident id; the live graph, which builds the same tools from the same specs, is step 5.

## Reproduce

Give a service a `repo` in the hosts file and set `GITHUB_TOKEN` in `.env`, then run the commands under [Setup](#setup). The script reads the first service with a `repo` in `BLEEP_HOSTS_FILE`. `SPIKE_PUBLIC_REPO` and `SPIKE_GUARD_SHAS` change the README repo and the commits read for the guard checks. It makes about 50 requests to GitHub for steps 2-3 and about 45 for the public repo's docs.
