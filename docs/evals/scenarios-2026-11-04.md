# Scenario evals, 2026-11-04

> `openai/gpt-6-luna` diagnosed and proposed for 8 demo faults, 1 run each; Claude (Opus 5.5) graded the root causes by hand. The root cause passed in 7 of 8 runs and the plan was exact in 7 of 8.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | Claude (Opus 5.5), by hand, against each scenario's expected root cause; every grade and its note in [`scenarios-20261104-075643.grades.jsonl`](data/scenarios-20261104-075643.grades.jsonl) |
| Scenarios | bad-deploy, bad-env, db-down, disk-full, hung, misroute, oom, port-conflict, 1 run each |
| When | 2026-11-04 07:56 UTC, 12 min |
| Commit | `527aebe` |
| Raw data | [`scenarios-20261104-075643.jsonl`](data/scenarios-20261104-075643.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --judge manual --repeat 1` (in `backend/`) |
| Graded | `uv run --group evals python -m evals.report ../docs/evals/data/scenarios-20261104-075643.jsonl --grades ../docs/evals/data/scenarios-20261104-075643.grades.jsonl --grader 'Claude (Opus 5.5)' --out ../docs/evals/scenarios-2026-11-04.md` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls; peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app.

## bad-deploy

- Fault: `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.95 pass | dependency | no action | yes | no | 8 | 36,811 | 7,801 | $0.0029 | 60 s | 75 s |

- Run 1 diagnosis: The shop-web application is running version 2 and requests the price_cents column, but that column is absent from the existing shop database. The schema migration adding the column was not applied, so /api/items returns HTTP 500.
  - Judge: Version 2 asks for price_cents, which the shop database lacks because the migration was not applied, so /api/items answers 500. That shop-web stays healthy is not in its root cause.

## bad-env

- Fault: `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.85 pass | network | no action | yes | no | 5 | 51,209 | 9,747 | $0.0049 | 15 s | 50 s |

- Run 1 diagnosis: shop-web exits during startup because it cannot resolve database hostname `db`. The database container is healthy, but the available evidence does not establish whether `db` is absent from its Compose network aliases or another DNS/network issue is responsible.
  - Judge: Right mechanism: shop-web exits at startup because the host db does not resolve while the database is healthy. It leaves open whether db is a missing alias or another DNS issue rather than naming the wrong DATABASE_URL, and is unsure (0.48).

## db-down

- Fault: `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | docker.start shop-db | yes | no | 5 | 24,959 | 7,782 | $0.0028 | 30 s | 52 s |

- Run 1 diagnosis: shop-db was cleanly stopped after startup, so Docker DNS no longer exposed shop-db and shop-web's database-dependent health checks and item requests failed. The available evidence does not establish why or by whom the database was stopped.
  - Judge: shop-db was stopped, so shop-web's database-dependent checks and item requests fail; says plainly it cannot tell who stopped it.

## disk-full

- Fault: `disk-full`
- Expected root cause: The shop's 20 MB uploads volume is full (No space left on device), so shop-web cannot write to it and its health check fails.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.85 pass | resource | no action | yes | no | 8 | 18,586 | 6,077 | $0.0021 | 30 s | 44 s |

- Run 1 diagnosis: The shop-web health check fails because the shared uploads volume is out of storage space; the worker is processing repeated 8 MB batches and uploads are accumulating there. The service remains running but unhealthy because upload write/check operations return no space left on device.
  - Judge: The shared uploads volume is out of space, so shop-web's health check fails with no space left on device. It adds that the worker's batches fill the volume, which the expected cause does not say.

## hung

- Fault: `hung`
- Expected root cause: shop-web hangs: the container runs but answers no request, so its health check times out.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.20 fail | unknown | no action | no | no | 8 | 27,676 | 6,407 | $0.0029 | 45 s | 68 s |

- Run 1 diagnosis: The cause of shop-web becoming unhealthy is undetermined. Available logs show successful health and API requests, but the health-check definition and failing probe result were not collected, so a faulty probe cannot be distinguished from an application or configuration issue.
  - Judge: Wrong: says the cause is undetermined and leans on successful health and API lines from before the hang; no hang is named. Same miss as since #106.

## misroute

- Fault: `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 4 | 26,349 | 7,306 | $0.0027 | 60 s | 79 s |

- Run 1 diagnosis: shop-proxy loaded a misrouted Caddy configuration pointing to shop-web on port 8001, while the web service listens on port 8000, so proxy requests failed with connection refused and returned 502. The web and database were running, so the proxy upstream configuration is the cause.
  - Judge: shop-proxy points at shop-web:8001 while shop-web listens on 8000, so the proxy answers 502.

## oom

- Fault: `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 6 | 49,907 | 7,175 | $0.0038 | 15 s | 55 s |

- Run 1 diagnosis: shop-worker is repeatedly OOM-killed because it attempts a 256 MB batch while its container is limited to 64 MiB. The worker cannot remain up under that memory mismatch; the exact Compose settings needed to specify a remedy were not collected.
  - Judge: The 256 MB batch exceeds shop-worker's 64 MiB limit, so it is OOM-killed again and again.

## port-conflict

- Fault: `port-conflict`
- Expected root cause: shop-canary holds port 8080, so shop-proxy cannot publish its port (port is already allocated) and does not start; the shop's URLs answer the canary instead.
- Expected category `network`, plan no action, never `docker.start`, `docker.stop`, `docker.restart`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 5 | 26,307 | 7,322 | $0.0033 | 60 s | 83 s |

- Run 1 diagnosis: shop-canary is occupying host port 8080 and serves a static response, so shop-proxy cannot bind that port and public checks receive the wrong content. The intended shop proxy is down due to the port collision.
  - Judge: shop-canary holds host port 8080, so shop-proxy cannot bind it and the checks get the canary's page.

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 36,811 | 7,801 | $0.0029 | 75 s |
| bad-env | 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 5.0 | 51,209 | 9,747 | $0.0049 | 50 s |
| db-down | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 5.0 | 24,959 | 7,782 | $0.0028 | 52 s |
| disk-full | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 18,586 | 6,077 | $0.0021 | 44 s |
| hung | 1 | 0 of 1 | 0 of 1 | 0 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 27,676 | 6,407 | $0.0029 | 68 s |
| misroute | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 4.0 | 26,349 | 7,306 | $0.0027 | 79 s |
| oom | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 6.0 | 49,907 | 7,175 | $0.0038 | 55 s |
| port-conflict | 1 | 1 of 1 | 0 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 5.0 | 26,307 | 7,322 | $0.0033 | 83 s |
| **All** | 8 | 7 of 8 | 4 of 8 | 7 of 8 | 0 of 8 | 0 | 0 | 0 | 6.1 | 32,726 | 9,747 | $0.0253 | 63 s |

## Cost

- Model under test: $0.0253.
- Judge: none, graded by hand.

## What it shows

- The root cause passed in 7 of 8 runs, and the category was right in 4 of 8.
- The plan was exactly the expected one in 7 of 8 runs; 0 proposed an unnecessary action.
- The largest single prompt was 9,747 tokens (bad-env, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.

## Against the last run

The only model-facing change since [scenarios with knowledge search](scenarios-2026-11-03-3.md) is `mcp.call` in the plan's action list (`schemas/graph.py`, #43); the MCP plugin itself was off, as on a fresh install.

- **Root causes held.** 7 of 8 again, the same seven passing; hung failed as it has since #106, saying the cause was not established.
- **Plans held or improved.** Exact in 7 of 8 against 6 of 8. No plan named `mcp.call` or any other action the plugin adds, and none proposed an unnecessary action.
- **Categories unchanged.** 4 of 8, the same four misses (bad-deploy `dependency`, bad-env `network`, hung `unknown`, port-conflict `config`) that #108 is open about.
- **Cost and size in the same range.** $0.0253 for 8 runs against $0.026; 4 to 8 tool calls per run; every prompt under 9,800 tokens.

## Limits

- **One run per scenario.** It shows the new action did not break a scenario; it cannot show a small change in pass rate.
- **With the plugin off.** No run had an MCP server, so this measures the plan schema only; the MCP tools' own behaviour is covered by the tests in `tests/plugins/test_mcp_url.py`.
