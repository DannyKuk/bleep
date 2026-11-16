# Scenario evals, 2026-11-16

> `openai/gpt-oss-20b` diagnosed and proposed for 5 demo faults and 1 look on request, 1 run each; `claude-code` judged the root causes. The root cause passed in 6 of 6 runs and the plan was exact in 5 of 6.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-oss-20b` (provider `local`) |
| Judge | `claude-code` through DeepEval GEval; a root cause passes at 0.7 or more |
| Scenarios | bad-deploy, bad-env, db-down, look-healthy, misroute, oom, 1 run each |
| When | 2026-11-16 10:51 UTC, 8 min |
| Commit | `25d85a22` |
| Raw data | [`scenarios-20261116-105120.jsonl`](data/scenarios-20261116-105120.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --only db-down,oom,bad-env,misroute,bad-deploy,look-healthy --repeat 1` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. A look on request switches no fault: once the watcher sees every container of the service healthy, the run asks for a look as a person's tap does, times count from then, and a look whose plan is no action goes on to Report, whose text is quoted. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app, and the linked repo docs `DannyKuk/bleep: demo/shop/docs/deploy.md`, `DannyKuk/bleep: demo/shop/docs/services.md`, read from this checkout.

## bad-deploy

- Opened by the fault `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | no action | yes | no | 1 | `recent_commits({"since_hours": 24})` | 23,596 | 7,211 | $0.0000 | 60 s | 76 s |

- Run 1 diagnosis: The shop‑web service was deployed with code that queries the column `price_cents` in table `items`, but the running shop-db container still has an older schema where that column does not exist. The new migration added by commit 3350063 was never applied to the database, causing every `/api/items` request to fail with a 500 error.
  - Judge: The diagnosis gives the same root cause as the expected output. shop-web's new code (from the commit that updated /api/items) reads the price_cents column in the items table. shop-db does not have that column because the migration was never applied, so every /api/items request returns a 500. It commits to this one cause rather than listing alternatives, and its log evidence (UndefinedColumn errors in both the web and db logs) and commit evidence are correct, consistent extra detail. It doesn't mention that shop-web stays healthy, but that is a symptom observation, not part of the cause.
  - Commits named by sha: the diagnosis `3350063`

- The diagnosis named a commit of the repo by its sha in 1 of 1 diagnosed runs (#350).

## bad-env

- Opened by the fault `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `DATABASE_URL`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 4 | `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 10})`, `recent_events({"minutes": 30})` | 41,678 | 7,480 | $0.0000 | 15 s | 36 s |

- Run 1 diagnosis: The shop‑web container’s DATABASE_URL environment variable points to host 'db' (DATABASE_URL=postgresql://…@db:5432/…) but the database service is named shop-db, so Docker DNS cannot resolve 'db', causing a startup failure.
  - Judge: The diagnosis gives the same root cause as the expected output. It names the right component and setting: shop-web's DATABASE_URL points to host 'db'. It also gives the right reason: the database service is called shop-db, so 'db' cannot be resolved. It commits to this one cause and labels it a config problem. Its evidence backs this up: the inspected environment variable, the shop-db container having no 'db' alias, and the log line 'failed to resolve host db'. It does not say that shop-web keeps being restarted, but that is a symptom rather than the cause, so this barely matters.
  - Names: the diagnosis names every one, the plan names every one

## db-down

- Opened by the fault `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | dependency | no action | no | no | 3 | `list_containers`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 5})` | 31,847 | 5,675 | $0.0000 | 30 s | 45 s |

- Run 1 diagnosis: The shop-db container exited normally at 10:54:49, causing the shop-web service to lose database connectivity and become unhealthy.
  - Judge: The diagnosis matches the expected root cause. It names the right component, the shop-db container, and the right reason it fails: the container is stopped, so shop-web cannot reach its database. The evidence supports this. The container is in the exited state with exit code 0 and was not OOMKilled. There is a 'die' event for shop-db at 10:54:49. The shop-web logs show failures to resolve the host 'shop-db'. The diagnosis commits to this one cause and does not hedge between several. It treats the unhealthy shop-web as a symptom, not the cause. The 'dependency' category fits. It does not say outright that the shop's API fails, but this is a minor omission because the cause and component are correct.

## look-healthy

- Opened by a look on request
- Expected root cause: Nothing is wrong with the shop: every container runs, none is unhealthy, and nothing shows a failure.
- Expected category `unknown`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | unknown | no action | yes | no | 0 | n/a | 11,129 | 4,079 | $0.0000 | 0 s | 10 s |

- Run 1 diagnosis: No failure was detected; all service components are running normally and responding correctly.
  - Judge: The expected output says nothing is wrong: every container runs, none is unhealthy, and nothing shows a failure. The diagnosis reaches the same conclusion and commits to it. It reports that no failure was detected and does not invent a fault or list several possible causes. Its evidence fits that conclusion: shop-proxy, shop-web, shop-db and shop-worker are all running with no restarts. shop-web served /health and /api/items with 200 OK, PostgreSQL is ready to accept connections, the worker processed batches without errors, and the URL check on /health returned 200. The only small flaw is the 'unknown' category, which is vague where a 'no fault' label would have been clearer, but it does not change the root-cause conclusion.
  - Report (Healthy when checked, nothing to do): At 10:56:04 a request was received to inspect the demo/compose:shop service. I performed a health check and verified that all containers (shop-proxy, shop-web, shop-db, shop-worker) were running without restarts and responding correctly. No issues were detected, so no action was taken. Cause: No failure was detected; all service components are running normally and responding correctly.

## misroute

- Opened by the fault `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | config | no action | yes | no | 3 | `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `knowledge_search({"query": "Caddyfile shop proxy 8001", "sources": ["repo"]})` | 36,422 | 7,453 | $0.0000 | 60 s | 81 s |

- Run 1 diagnosis: The Caddy proxy was configured to forward requests to http://shop-web:8001, but shop‑web listens on port 8000; the hard‑coded upstream in the baked Caddyfile caused all health checks to return 502.
  - Judge: The diagnosis identifies the same root cause as the expected output. It names the same failing component and the same reason: shop-proxy (Caddy) forwards to shop-web:8001, but shop-web listens on 8000, so requests through the proxy get 502s. The evidence supports this. The proxy logs show 'connection refused' on port 8001, and the container inspection shows shop-web exposes only port 8000. The 'config' category is correct. The extra detail about the hard-coded upstream in the baked Caddyfile fits the cause and does not hurt. One small gap: it says 'all health checks' return 502, which is narrower than every proxied request. It also does not say outright that shop-web itself is healthy.

## oom

- Opened by the fault `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `WORKER_BATCH_MB`, `WORKER_BATCH_MB=8`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 2 | `inspect_container({"container": "shop-worker"})`, `knowledge_search({"query": "WORKER_BATCH_MB", "sources": ["repo"]})` | 19,024 | 4,337 | $0.0000 | 15 s | 28 s |

- Run 1 diagnosis: The environment variable `WORKER_BATCH_MB` was set to **256**, causing the shop‑worker process to allocate a 256 MiB batch buffer while the container is limited to **64 MiB** of memory, leading to an OOM kill and restart loop.
  - Judge: The diagnosis names the same failing component (shop-worker) and the same reason it fails: WORKER_BATCH_MB=256 is more than the 64 MiB container memory limit, so the container is OOM-killed and restarts in a loop. This matches the expected root cause exactly. It commits to one cause instead of hedging, and its evidence backs it up: the inspect_container output shows the 64.0 MiB limit, exit code 137, OOMKilled true and WORKER_BATCH_MB=256, and the repo docs confirm the 64 MiB limit. The 'config' category is also correct. The only extra claim, that the process allocates a 256 MiB buffer, is reasonable added detail and does not change the root cause.
  - Names: the diagnosis leaves out `WORKER_BATCH_MB=8`, the plan leaves out `WORKER_BATCH_MB=8`

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 1.0 | 23,596 | 7,211 | $0.0000 | 76 s |
| bad-env | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 4.0 | 41,678 | 7,480 | $0.0000 | 36 s |
| db-down | 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 of 1 | 0 | 0 | 0 | 3.0 | 31,847 | 5,675 | $0.0000 | 45 s |
| look-healthy | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 0.0 | 11,129 | 4,079 | $0.0000 | 10 s |
| misroute | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 3.0 | 36,422 | 7,453 | $0.0000 | 81 s |
| oom | 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 2.0 | 19,024 | 4,337 | $0.0000 | 28 s |
| **All** | 6 | 6 of 6 | 4 of 6 | 5 of 6 | 0 of 6 | 0 | 0 | 0 | 2.2 | 27,283 | 7,480 | $0.0000 | 46 s |

## Cost

- Model under test: $0.0000 (a local model costs nothing per call).
- Judge: n/a (billed to a plan, not per call).

## What it shows

- The root cause passed in 6 of 6 runs, and the category was right in 4 of 6.
- The plan was exactly the expected one in 5 of 6 runs; 0 proposed an unnecessary action.
- The diagnosis named everything its fix needs in 1 of 2 runs that score it, and the plan in 1 of 2.
- The largest single prompt was 7,480 tokens (bad-env, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
