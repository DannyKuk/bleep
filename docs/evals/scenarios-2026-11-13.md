# Scenario evals, 2026-11-13

> **Answer:** on `claude-sonnet-5-5` through the Anthropic API, with structured output through `json_schema` (#515, PR #551) and only the investigate loop cached (#546), incidents run end to end and structured output never fell back to JSON: 0 fallbacks in 8 runs, where a live look before the change fell back in 2 of 3 structured calls ([anthropic-cache-2026-11-13](anthropic-cache-2026-11-13.md)). The root cause passed in 8 of 8 runs and the plan was exact in 6 of 8. The round cost $0.685 for the model under test ($0.065 to $0.110 a run) and $0.001 for the judge (`openai/gpt-6-luna`).

Two runs fell short, and neither because of structured output. **hung:** after a right diagnosis, the Anthropic API answered `503 credential validation failed` three times in a row, so the guard gave the model up as unavailable and the run ended without a plan. That was an API outage, not Bleep. **misroute:** it proposed `docker.restart shop-proxy`. The scenario expects no action and forbids a restart. A restart would drop the hot-loaded Caddy config, but Bleep cannot know the file on disk is the right one.

> `claude-sonnet-5-5` diagnosed and proposed for 8 demo faults, 1 run each; `openai/gpt-6-luna` judged the root causes. The root cause passed in 8 of 8 runs and the plan was exact in 6 of 8.

## Setup

| | |
|---|---|
| Model under test | `claude-sonnet-5-5` (provider `anthropic`) |
| Judge | `openai/gpt-6-luna` through DeepEval GEval; a root cause passes at 0.7 or more |
| Scenarios | db-down, bad-deploy, bad-env, disk-full, hung, misroute, oom, port-conflict, 1 run each |
| When | 2026-11-13 13:12 UTC, 13 min |
| Commit | `a0c2d981` |
| Raw data | [`scenarios-20261113-131246.jsonl`](data/scenarios-20261113-131246.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --only oom,hung,disk-full,port-conflict,bad-env,bad-deploy,misroute --repeat 1 --append data/evals/scenarios-20261113-131246.jsonl` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app, and the linked repo docs `DannyKuk/bleep: demo/shop/docs/deploy.md`, `DannyKuk/bleep: demo/shop/docs/services.md`, read from this checkout.

## db-down

- Opened by the fault `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | docker.start shop-db | yes | no | 3 | `inspect_container({"container": "shop-db"})`, `container_logs({"container": "shop-db", "tail": 50})`, `recent_events({"minutes": 30})` | 30,418 | 9,224 | $0.0645 | 30 s | 43 s |

- Run 1 diagnosis: The shop-db container (postgres:17-alpine) was stopped at 13:13:21 after a kill and stop request, with a clean shutdown and exit code 0, and nothing restarted it. shop-web can no longer resolve the host 'shop-db' from DATABASE_URL, so /health returns 503 and /api/items returns 500.
  - Judge: The diagnosis matches the expected cause: shop-db is stopped, preventing shop-web from connecting to the database and causing the API to fail. The clean shutdown and subsequent health-check and API failures provide consistent supporting evidence.

## bad-deploy

- Opened by the fault `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | code | no action | yes | no | 6 | `deployed_commits`, `inspect_container({"container": "shop-web"})`, `recent_commits({"since_hours": 72})`, `knowledge_search({"query": "price_cents column migration items table", "sources": ["repo", "inci…)`, `commit_diff({"sha": "3350063"})`, `knowledge_search({"query": "how is the migration run, SHOP_VERSION 2, rollback", "sources": ["re…)` | 72,112 | 15,286 | $0.1019 | 60 s | 83 s |

- Run 1 diagnosis: shop-web runs shop version 2 (SHOP_VERSION, whose documented default is 1), and its /api/items reads the price_cents column added by commit 3350063 'Keep item prices in whole cents for shop 2'. The items table in shop-db never received migrations/0002_price_cents.sql, so every query fails with a missing-column error and returns 500. The code and the database schema do not match.
  - Judge: The diagnosis matches the expected root cause: shop-web version 2 queries price_cents, but shop-db lacks the column because its migration never ran, causing /api/items to return 500 while the web container remains healthy.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`

- The diagnosis named a commit of the repo by its sha in 1 of 1 diagnosed runs (#350).

## bad-env

- Opened by the fault `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `DATABASE_URL`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 4 | `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-worker"})`, `knowledge_search({"query": "DATABASE_URL", "sources": ["repo"]})` | 46,151 | 11,491 | $0.0799 | 15 s | 32 s |

- Run 1 diagnosis: shop-web's DATABASE_URL in demo/compose.yaml names the host 'db', which is no container or network alias; the database container is shop-db. The name fails to resolve at startup, so shop-web exits with code 3 and loops under restart policy unless-stopped. The docs give the host and port as shop-db:5432.
  - Judge: The diagnosis matches the expected root cause: shop-web's DATABASE_URL uses the nonexistent host `db` instead of `shop-db`, so it cannot reach the database and exits at startup under the restart policy. The evidence that shop-db is healthy and the docs specify shop-db:5432 supports this conclusion.
  - Names: the diagnosis names every one, the plan names every one

## disk-full

- Opened by the fault `disk-full`
- Expected root cause: The shop's 20 MB uploads volume is full (No space left on device), so shop-web cannot write to it and its health check fails.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 8 | `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-worker", "tail": 50})`, `recent_events({"minutes": 60})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-proxy", "tail": 30})`, `knowledge_search({"query": "uploads volume No space left on device shop-web"})`, `knowledge_search({"query": "WORKER_BATCH_MB", "sources": ["repo"]})` | 35,466 | 9,345 | $0.0771 | 30 s | 47 s |

- Run 1 diagnosis: The `uploads` volume, a 20 MB in-memory (tmpfs) volume mounted at /uploads, is full. shop-web's /health check writes to it and fails with 'No space left on device', so shop-web reports unhealthy and returns 503 on /health.
  - Judge: The diagnosis matches the expected root cause: the 20 MB uploads volume is full, causing shop-web writes to fail with “No space left on device” and its health check to fail. The evidence that /api/items still returns 200 and the container is not resource-starved supports the same cause rather than merely describing a symptom.

## hung

- Opened by the fault `hung`
- Expected root cause: shop-web hangs: the container runs but answers no request, so its health check times out.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | unknown | none | no | no | 10 | `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 60})`, `container_logs({"container": "shop-worker", "tail": 50})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-web", "tail": 50})`, `container_logs({"container": "shop-proxy", "tail": 30})`, `knowledge_search({"query": "SHOP_DEBUG_HOOK health endpoint hangs", "sources": ["repo", "inciden…)`, `container_logs({"container": "shop-db", "tail": 30})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-worker"})` | 68,695 | 11,807 | $0.1100 | 45 s | 87 s |

- Run 1 failed (error): ModelUnavailable: the model is unavailable (AnthropicAPIError)
- Run 1 diagnosis: shop-web is running, but its /health endpoint stopped answering within the 3 s probe timeout after the first probe passed at 13:19:01, so Docker marked it unhealthy. The cause of the hang is not established: shop-web's environment (SHOP_DEBUG_HOOK, DATABASE_URL) was not seen, and neither the database query nor the uploads write that /health performs was tested.
  - Judge: The diagnosis matches the expected cause: shop-web remains running but hangs and stops answering requests, causing the health check to time out. Its evidence that later probes timed out and the container stayed running supports this; the evaluation does not require identifying what caused the hang.

## misroute

- Opened by the fault `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | docker.restart shop-proxy | no | yes | 3 | `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})` | 34,438 | 10,240 | $0.0827 | 60 s | 81 s |

- Run 1 diagnosis: The running shop-proxy (Caddy) was hot-loaded at 13:21:01 through its admin API from /etc/caddy/Caddyfile.misroute, which sends upstream traffic to 172.23.0.4:8001. Nothing listens on 8001, because shop-web serves on port 8000, so every request gets a 502. The original /etc/caddy/Caddyfile worked until that load.
  - Judge: The diagnosis identifies the same root cause: shop-proxy routes requests to shop-web on port 8001, where nothing listens, while shop-web is healthy and listens on 8000. The evidence that the misrouted config was hot-loaded adds correct detail.
  - Forbidden steps proposed: docker.restart shop-proxy

## oom

- Opened by the fault `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `WORKER_BATCH_MB`, `WORKER_BATCH_MB=8`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 6 | `inspect_container({"container": "shop-worker"})`, `knowledge_search({"query": "shop-worker batch size setting memory limit", "sources": ["repo"]})`, `knowledge_search({"query": "WORKER_BATCH_MB", "sources": ["repo"]})`, `knowledge_search({"query": "worker batch size in MB, how to size WORKER_BATCH_MB relative to mem…)`, `knowledge_search({"query": "shop-worker environment variables configuration compose.yaml", "sour…)`, `knowledge_search({"query": "shop-worker OOM killed batch too large", "sources": ["incident"]})` | 58,642 | 9,392 | $0.1016 | 15 s | 73 s |

- Run 1 diagnosis: shop-worker's batch size, set by the environment variable WORKER_BATCH_MB and logged as 256 MB, is far above the container's 64 MiB memory limit (no swap). It is OOM-killed (exit 137) after loading about 48 MB of each batch, and `unless-stopped` restarts it into the same crash. The docs give no value for WORKER_BATCH_MB, only that it must stay well under 64 MB. The setting lives in demo/compose.yaml.
  - Judge: The diagnosis matches the expected cause: shop-worker has WORKER_BATCH_MB set to 256, exceeding its 64 MB memory limit, so it is OOM-killed and repeatedly restarts. The extra evidence is consistent with that cause.
  - Names: the diagnosis leaves out `WORKER_BATCH_MB=8`, the plan leaves out `WORKER_BATCH_MB=8`

## port-conflict

- Opened by the fault `port-conflict`
- Expected root cause: shop-canary holds port 8080, so shop-proxy cannot publish its port (port is already allocated) and does not start; the shop's URLs answer the canary instead.
- Expected category `network`, plan no action, never `docker.start`, `docker.stop`, `docker.restart`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.70 pass | network | no action | yes | no | 4 | `inspect_container({"container": "shop-proxy"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-web", "tail": 50})`, `recent_events({"minutes": 240})` | 37,527 | 8,747 | $0.0677 | 60 s | 77 s |

- Run 1 diagnosis: shop-proxy could not start because host port 8080 was already allocated: Docker failed to bind 0.0.0.0:8080 when the container was recreated, so it exited with code 128. Something else on demo is holding 8080 and answering the probes with a reply that lacks the expected text; I could not identify what it is.
  - Judge: The diagnosis correctly identifies that shop-proxy cannot start because port 8080 is already allocated, and that another listener answers the shop URLs. However, it says the listener could not be identified instead of naming shop-canary, the expected process holding the port.

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| db-down | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 3.0 | 30,418 | 9,224 | $0.0645 | 43 s |
| bad-deploy | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 6.0 | 72,112 | 15,286 | $0.1019 | 83 s |
| bad-env | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 4.0 | 46,151 | 11,491 | $0.0799 | 32 s |
| disk-full | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 35,466 | 9,345 | $0.0771 | 47 s |
| hung | 1 | 1 of 1 | 0 of 1 | 0 of 1 | 0 of 1 | 1 | 0 | 0 | 10.0 | 68,695 | 11,807 | $0.1100 | 87 s |
| misroute | 1 | 1 of 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 | 0 | 0 | 3.0 | 34,438 | 10,240 | $0.0827 | 81 s |
| oom | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 6.0 | 58,642 | 9,392 | $0.1016 | 73 s |
| port-conflict | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 4.0 | 37,527 | 8,747 | $0.0677 | 77 s |
| **All** | 8 | 8 of 8 | 7 of 8 | 6 of 8 | 1 of 8 | 1 | 0 | 0 | 5.5 | 47,931 | 15,286 | $0.6854 | 65 s |

## Cost

- Model under test: $0.6854.
- Judge: $0.0012.

## What it shows

- The root cause passed in 8 of 8 runs, and the category was right in 7 of 8.
- The plan was exactly the expected one in 6 of 8 runs; 1 proposed an unnecessary action.
- The diagnosis named everything its fix needs in 1 of 2 runs that score it, and the plan in 1 of 2.
- The largest single prompt was 15,286 tokens (bad-deploy, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
- 1 run failed (hung run 1); the errors are listed above.
