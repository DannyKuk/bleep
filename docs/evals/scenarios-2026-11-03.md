# Scenario evals, 2026-11-03

> **Answer:** On Bleep's default model, `openai/gpt-6-luna`, the root cause was right in **21 of 24** runs (8 demo faults, 3 runs each, graded by hand), and the plan was exactly the expected one in **18 of 24**, with 3 unnecessary actions. That took about 70 s from the fault to the diagnosis and $0.0035 a run. The three misses were all hung, and log leftovers caused them: Round 1 ran on a shop that is never reset, and hung came right after disk-full. **hung: 0 of 3 on the stale shop, 3 of 3 on a reset shop** (Round 2). Its plan was still 0 of 3, because the fault's own log line says the freeze is deliberate (#106).

## Question

How well does Bleep find the root cause of each of the eight demo faults and propose the right plan, on the model it ships with, and at what cost in tool calls, tokens, money and time?

## Rounds

| Round | Model under test | Runs | Shop between runs | Root cause right | Plan exact | Cost |
|---|---|---|---|---|---|---|
| [0](#round-0-local-qwen-smoke-pass-stopped) | `huihui-qwen3.8-27b-abliterated`, local | 8 (1 per fault) | not reset | 5 of 8 | 5 of 8 | $0 (local) |
| [1](#round-1-the-luna-baseline) | `openai/gpt-6-luna` | 24 (3 per fault) | not reset | 21 of 24 | 18 of 24 | $0.0843 |
| [2](#round-2-hung-on-a-reset-shop) | `openai/gpt-6-luna` | 3 (hung) | recreated before each run | 3 of 3 | 0 of 3 | $0.0067 |

The headline numbers are Round 1 as measured. Round 2 sits next to it for hung only; the two are not merged.

## Round 0: local Qwen smoke pass (stopped)

The suite first ran on the local model planned for this task: `huihui-qwen3.8-27b-abliterated` in LM Studio on a 16,128-token context, with 1 parallel slot. It is a reasoning model, and its refusal training has been removed ("abliterated"). The pass was 1 run per fault, scenarios at 900 s, and the GEval judge on `openai/gpt-6-luna`. It was stopped after this one pass, for three reasons:

- A whole run took 8 to 14 minutes.
- The model's thinking cannot be switched off: `reasoning_effort`, `/no_think` and `enable_thinking: false` were each tried.
- 2 of the 8 runs ran out of context at 16k.

The baseline moved to Bleep's default cloud model (Round 1), and the local model gets its own report in #35.

| Scenario | Result | Root cause score | Peak prompt | Minutes to diagnosis |
|---|---|---|---|---|
| bad-deploy | right, plan exact, category `config` (expected `code`) | 0.90 | 12,355 | 9.2 |
| bad-env | right, plan exact | 1.00 | 9,612 | 6.9 |
| db-down | right, plan exact | 1.00 | 12,490 | 5.0 |
| disk-full | right, plan exact | 1.00 | 9,286 | 5.5 |
| hung | failed, out of context in Diagnose: an 8,095-token prompt, then 8,008 tokens of reasoning | n/a | 16,002 | n/a |
| misroute | failed: the model server answered HTTP 400 before any model call was recorded | n/a | 0 | n/a |
| oom | right, plan exact, category `config` (expected `resource`) | 1.00 | 5,468 | 2.4 |
| port-conflict | failed, out of context: a 13,195-token prompt, then 2,915 tokens of reasoning | n/a | 14,346 | n/a |

The raw runs are in [`scenarios-20261103-104334.jsonl`](data/scenarios-20261103-104334.jsonl). The output-token cap that this pass prompted was not needed on the cloud model and was not built; #35 takes the context question up.

## Round 1: the luna baseline

### Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`), Bleep's default `LLM_MODEL` |
| Judge | Claude (Opus 5.5), by hand, against each scenario's expected root cause; every grade and its note in [`scenarios-20261103-124139.grades.jsonl`](data/scenarios-20261103-124139.grades.jsonl) |
| Scenarios | bad-deploy, bad-env, db-down, disk-full, hung, misroute, oom, port-conflict, 3 runs each, 400 s each |
| When | 2026-11-03, in three batches of one run per scenario: 12:41 to 12:55, 13:13 to 13:28 and 13:30 to 13:44 UTC. The demo shop and its lock were shared with the injection evals (#34) between batches |
| Order | alphabetical within each batch, as above, on a demo shop that is never reset between runs |
| Commit | `d715fc9` |
| Raw data | [`scenarios-20261103-124139.jsonl`](data/scenarios-20261103-124139.jsonl), with its setup in [`scenarios-20261103-124139.info.json`](data/scenarios-20261103-124139.info.json) |

Each run switches the fault on and lets Bleep's watcher open the incident. It runs the graph to the end of Propose, then switches the fault off; nothing is executed. Times count from the fault going on. Steps are the model's tool calls, and peak prompt is the largest single prompt of the run. "Unnecessary" means the plan acts beyond the expected one, or proposes an action the scenario forbids.

Before the suite, a single timing-check run of bad-deploy at the old 900 s timeout took 138 s from the fault to the end of Propose. That set the 400 s timeout. It scored 0.75 (pass), category `dependency`, plan exact, 109,345 tokens with a 22,928-token peak prompt, and $0.0074. It is kept in [`scenarios-20261103-123850.jsonl`](data/scenarios-20261103-123850.jsonl), with [its grade](data/scenarios-20261103-123850.grades.jsonl), and it is not counted in the tables below.

### bad-deploy

- Fault: `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.85 pass | dependency | no action | yes | no | 4 | 39,166 | 10,482 | $0.0033 | 60 s | 84 s |
| 2 | 0.80 pass | dependency | no action | yes | no | 7 | 88,921 | 19,830 | $0.0069 | 60 s | 100 s |
| 3 | 0.85 pass | code | no action | yes | no | 5 | 52,066 | 12,126 | $0.0040 | 60 s | 111 s |

- Run 1 diagnosis: The shop API fails because the deployed shop-web code selects price_cents, but that column is absent from the persistent shop-db items schema. This application/database schema mismatch causes /api/items to return HTTP 500 while health checks remain successful.
  - Judge: Names the mechanism: the deployed code selects price_cents, which the items table lacks, so /api/items returns 500 while health stays green. Doesn't name the SHOP_VERSION=2 change or the migration that never ran.
- Run 2 diagnosis: The shop API fails because shop-web queries the items table for price_cents, which does not exist in the shop-db schema. This is an application/database schema mismatch; the collected evidence does not establish the exact schema change or a verified recovery remedy.
  - Judge: Same mechanism (query for a missing price_cents column, a schema mismatch); unsure, and doesn't tie it to the new version.
- Run 3 diagnosis: The shop items endpoint fails because shop-web selects the price_cents column, which is absent from the existing items table in shop-db. This is an application/database schema mismatch, leaving /api/items returning HTTP 500.
  - Judge: Same mechanism, stated with confidence; the version change and the missing migration are not named.

### bad-env

- Fault: `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 7 | 34,020 | 9,423 | $0.0026 | 15 s | 71 s |
| 2 | 0.90 pass | network | no action | yes | no | 8 | 48,793 | 12,957 | $0.0047 | 15 s | 44 s |
| 3 | 1.00 pass | config | no action | yes | no | 8 | 32,300 | 8,541 | $0.0028 | 15 s | 58 s |

- Run 1 diagnosis: shop-web is repeatedly exiting during startup because its database connection hostname `db` cannot be resolved from the container. The database is healthy, so the failure is a service discovery/configuration mismatch between shop-web's database URL and the available Compose service hostname/network alias.
  - Judge: Names the hostname db in shop-web's database URL as unresolvable, so it exits at startup and restarts; the database itself is healthy.
- Run 2 diagnosis: shop-web is failing during startup because its configured database hostname `db` does not resolve in its Docker DNS context, so database initialization fails and the process exits repeatedly. The precise Compose hostname or network misconfiguration is not verified from the collected evidence.
  - Judge: Right cause (db doesn't resolve, so startup fails and repeats), framed as a DNS or network problem rather than a wrong setting, and unsure.
- Run 3 diagnosis: shop-web is restarting because its database connection configuration points to the hostname `db`, which Docker cannot resolve from the container. The database container is healthy, so the failure is the web service's database hostname/network configuration, not database availability.
  - Judge: Names the database URL's hostname db as the wrong setting; the database is healthy.

### db-down

- Fault: `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | docker.start shop-db | yes | no | 8 | 22,517 | 6,758 | $0.0022 | 30 s | 54 s |
| 2 | 1.00 pass | dependency | docker.start shop-db, docker.restart shop-web | no | yes | 7 | 37,380 | 11,219 | $0.0032 | 30 s | 58 s |
| 3 | 1.00 pass | dependency | docker.start shop-db | yes | no | 8 | 36,561 | 10,580 | $0.0029 | 30 s | 57 s |

- Run 1 diagnosis: The shop database container exited cleanly and was no longer running, so Docker could not resolve shop-db and shop-web health checks and item requests failed. Starting shop-db is the recovery action; the evidence does not establish what caused it to stop.
  - Judge: shop-db stopped (exit 0), so shop-web can't resolve it and its checks fail.
- Run 2 diagnosis: The shop database container exited cleanly, leaving shop-web unable to resolve shop-db; database-dependent health checks and item requests then failed. The evidence establishes the database outage as the cause but does not establish what triggered its shutdown.
  - Judge: Right root cause. The extra restart of shop-web is a plan problem (scored by the harness), not a root-cause one.
  - Forbidden steps proposed: docker.restart shop-web
- Run 3 diagnosis: The shop database container exited cleanly and was left stopped, so Docker DNS no longer resolves shop-db; shop-web's database-dependent health checks and item requests then failed. The collected evidence does not identify why the database exited, but starting the stopped database is the indicated recovery.
  - Judge: shop-db stopped, so DNS no longer resolves it and shop-web fails.

### disk-full

- Fault: `disk-full`
- Expected root cause: The shop's 20 MB uploads volume is full (No space left on device), so shop-web cannot write to it and its health check fails.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 8 | 24,055 | 6,886 | $0.0023 | 30 s | 56 s |
| 2 | 0.95 pass | resource | no action | yes | no | 8 | 24,998 | 7,431 | $0.0024 | 30 s | 43 s |
| 3 | 1.00 pass | resource | no action | yes | no | 8 | 25,850 | 7,925 | $0.0025 | 30 s | 46 s |

- Run 1 diagnosis: The shop-web health check failed because the uploads filesystem/volume ran out of space, causing writes or its upload check to return ENOSPC. An earlier brief loss of shop-db name resolution recovered before the incident trigger and is not the cause of the 12:47:11 unhealthy event.
  - Judge: The uploads volume is out of space (ENOSPC). It correctly set aside leftover shop-db DNS errors from the db-down runs as not the cause.
- Run 2 diagnosis: The shop-web health check is failing because its uploads storage has no space available, causing upload checks to return ENOSPC. Earlier database hostname resolution failures were temporary; the database is now running and healthy.
  - Judge: Right cause (ENOSPC on uploads), at lower confidence; it set aside the leftover DNS errors.
- Run 3 diagnosis: The shop-web health check is failing because the uploads filesystem is out of space, returning ENOSPC. Earlier database name-resolution failures recovered before the incident trigger and are not the cause of the 13:36:48 unhealthy event.
  - Judge: Right cause, and it explicitly set aside the earlier DNS failures.

### hung

- Fault: `hung`
- Expected root cause: shop-web's event loop is blocked (a freeze request), so it answers no request and its health check times out, although the container is still running.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.30 fail | unknown | no action | no | no | 10 | 58,894 | 13,157 | $0.0047 | 45 s | 89 s |
| 2 | 0.00 fail | resource | no action | no | no | 8 | 23,170 | 6,513 | $0.0021 | 45 s | 77 s |
| 3 | 0.00 fail | resource | no action | no | no | 8 | 26,030 | 7,893 | $0.0027 | 45 s | 65 s |

- Run 1 diagnosis: The shop-web health check failure at 12:49 cannot be attributed confidently from the collected evidence. Upload storage exhaustion caused earlier failures, and a subsequent event-loop-block warning could explain the incident-time failure, but the exact 12:49 check result and current volume capacity are unavailable.
  - Judge: Wrong. It doesn't commit to a cause and leans on upload-space errors that are leftovers from the disk-full runs before it; the event-loop block is mentioned only as a possibility. Contaminated by stale logs (#104).
- Run 2 diagnosis: The shop-web health check is failing because its uploads volume is out of space, returning ENOSPC. The earlier database name-resolution failure was transient and recovered; the current unhealthy event follows repeated upload-space failures.
  - Judge: Wrong. It blames the full uploads volume, a leftover from the disk-full runs that ran just before; the blocked event loop isn't named. Contaminated by stale logs (#104).
- Run 3 diagnosis: The shop worker continuously wrote 8 MB batches to the uploads volume, filling its available storage; shop-web then failed its uploads health check with no space left on device. Earlier database name-resolution failures were transient, but uploads exhaustion is the direct cause of the latest unhealthy state.
  - Judge: Wrong. It blames the worker filling the uploads volume (disk-full leftovers); the blocked event loop isn't named. Contaminated by stale logs (#104).

### misroute

- Fault: `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 8 | 56,461 | 14,858 | $0.0045 | 60 s | 92 s |
| 2 | 1.00 pass | config | no action | yes | no | 8 | 55,980 | 14,387 | $0.0051 | 60 s | 109 s |
| 3 | 1.00 pass | config | no action | yes | no | 8 | 56,411 | 14,480 | $0.0047 | 60 s | 85 s |

- Run 1 diagnosis: The shop proxy was reconfigured to send requests to the web container on port 8001, but the web app listens on port 8000. The resulting refused upstream connections caused the observed 502 responses.
  - Judge: The proxy forwards to port 8001 while shop-web listens on 8000, so requests get 502s.
- Run 2 diagnosis: The shop proxy was reloaded with a misrouted upstream pointing to port 8001, while shop-web serves on its normal application port; requests therefore received connection-refused 502 responses. The proxy configuration change is the direct cause of the reported failures.
  - Judge: Misrouted upstream to 8001, connection refused, 502s.
- Run 3 diagnosis: The proxy was reloaded with a misrouted upstream pointing to port 8001, while shop-web listens on port 8000. Requests therefore received 502 connection refusals despite the application container recovering and running.
  - Judge: Misrouted upstream to 8001 while shop-web listens on 8000.

### oom

- Fault: `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 7 | 26,035 | 6,829 | $0.0025 | 15 s | 41 s |
| 2 | 1.00 pass | config | no action | yes | no | 4 | 22,251 | 5,511 | $0.0022 | 15 s | 65 s |
| 3 | 1.00 pass | resource | no action | yes | no | 5 | 17,317 | 5,167 | $0.0019 | 15 s | 29 s |

- Run 1 diagnosis: shop-worker is repeatedly OOM-killed because its 256 MB batch workload exceeds its 64 MiB container memory limit. Its automatic restart policy then places it in a restart loop; the worker is the affected service.
  - Judge: A 256 MB batch against a 64 MiB limit gives repeated OOM kills and a restart loop.
- Run 2 diagnosis: shop-worker is repeatedly OOM-killed because its configured 256 MB batch workload exceeds its 64 MiB container memory limit, leaving it in a restart loop.
  - Judge: Same, exact. Its category 'config' is defensible, since the cause is a setting, though the scenario expects 'resource'.
- Run 3 diagnosis: shop-worker's configured 256 MB batch exceeds its 64 MiB container memory limit, causing repeated OOM kills and a restart loop. This leaves the worker unavailable while shop-web, shop-proxy, and shop-db remain running.
  - Judge: Same, exact.

### port-conflict

- Fault: `port-conflict`
- Expected root cause: shop-canary holds port 8080, so shop-proxy cannot publish its port (port is already allocated) and does not start; the shop's URLs answer the canary instead.
- Expected category `network`, plan no action, never `docker.start`, `docker.stop`, `docker.restart`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | docker.stop shop-canary, docker.start shop-proxy | no | yes | 8 | 55,431 | 14,779 | $0.0050 | 60 s | 93 s |
| 2 | 1.00 pass | config | no action | yes | no | 8 | 54,531 | 14,385 | $0.0047 | 60 s | 99 s |
| 3 | 0.90 pass | config | docker.stop shop-canary, docker.start shop-proxy | no | yes | 8 | 53,089 | 14,088 | $0.0044 | 60 s | 79 s |

- Run 1 diagnosis: The shop-proxy could not start because shop-canary already held host port 8080. Requests therefore reached the canary instead of the shop proxy, returning HTTP 200 responses with content that failed the checks.
  - Judge: shop-canary holds host port 8080, so shop-proxy can't start and the URLs answer the canary. The plan (stop the canary, start the proxy) would work, but it stops a container outside the incident's service, which the scenario leaves to the human.
  - Forbidden steps proposed: docker.stop shop-canary, docker.start shop-proxy
- Run 2 diagnosis: The shop proxy replacement could not bind host port 8080 because the running shop-canary container already owned that port. Requests therefore reached the canary's static server instead of the shop application, producing HTTP 200 responses with unexpected content.
  - Judge: The canary owns 8080, so the proxy can't bind and requests reach the canary.
- Run 3 diagnosis: A replacement shop proxy was started as a canary using the shop-proxy service identity and bound host port 8080, leaving the actual shop-proxy container unable to bind that already-allocated port and exited. Requests at the incident times therefore reached the canary's static server instead of the shop API, so the expected API response text was absent.
  - Judge: Right core (the canary holds 8080, the proxy can't bind and exits), with a confused aside that the canary used shop-proxy's service identity. The same stop-canary plan as run 1.
  - Forbidden steps proposed: docker.stop shop-canary, docker.start shop-proxy

### Round 1 totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 3 | 3 of 3 | 1 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 5.3 | 60,051 | 19,830 | $0.0142 | 98 s |
| bad-env | 3 | 3 of 3 | 2 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 7.7 | 38,371 | 12,957 | $0.0102 | 58 s |
| db-down | 3 | 3 of 3 | 3 of 3 | 2 of 3 | 1 of 3 | 0 | 0 | 7.7 | 32,153 | 11,219 | $0.0083 | 56 s |
| disk-full | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 8.0 | 24,968 | 7,925 | $0.0073 | 49 s |
| hung | 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 | 0 | 8.7 | 36,031 | 13,157 | $0.0095 | 77 s |
| misroute | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 8.0 | 56,284 | 14,858 | $0.0143 | 96 s |
| oom | 3 | 3 of 3 | 2 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 5.3 | 21,868 | 6,829 | $0.0066 | 45 s |
| port-conflict | 3 | 3 of 3 | 0 of 3 | 1 of 3 | 2 of 3 | 0 | 0 | 8.0 | 54,350 | 14,779 | $0.0141 | 90 s |
| **All** | 24 | 21 of 24 | 14 of 24 | 18 of 24 | 3 of 24 | 0 | 0 | 7.3 | 40,509 | 19,830 | $0.0843 | 71 s |

### Cost

- Model under test: $0.0843.
- Judge: none, graded by hand.

## Round 2: hung on a reset shop

hung came right after disk-full in Round 1, and all three of its diagnoses blamed the upload-space errors that disk-full had left in the logs (see [What it shows](#what-it-shows)). Round 2 re-ran hung 3 times on a clean shop. Before each run, it switched every fault off and ran `docker compose -f demo/compose.yaml up -d --force-recreate`. That recreates every shop container, shop-db included, with fresh logs and the volumes kept. Then it waited until shop-web and shop-db were healthy and `/api/items` answered 200, and 15 s more. It then ran `--only hung --repeat 1`, appending runs 2 and 3. It ran at 13:47 to 13:53 UTC, at commit `d715fc9`, with the same model and the same judge by hand. Raw data: [`scenarios-20261103-134753.jsonl`](data/scenarios-20261103-134753.jsonl), grades: [`scenarios-20261103-134753.grades.jsonl`](data/scenarios-20261103-134753.grades.jsonl).

### hung, reset shop

- Fault: `hung`
- Expected root cause: shop-web's event loop is blocked (a freeze request), so it answers no request and its health check times out, although the container is still running.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | code | no action | no | no | 9 | 36,233 | 7,114 | $0.0033 | 45 s | 85 s |
| 2 | 1.00 pass | code | no action | no | no | 8 | 15,458 | 4,649 | $0.0017 | 45 s | 69 s |
| 3 | 0.95 pass | code | no action | no | no | 8 | 16,375 | 5,727 | $0.0017 | 45 s | 68 s |

- Run 1 diagnosis: shop-web’s application event loop was deliberately blocked after a freeze request, leaving the process running but unable to serve health checks and causing the container to become unhealthy. The evidence does not establish what requested the freeze or whether it reflects intended test behavior or a defect.
  - Judge: Right: a freeze blocked the event loop, so the process runs but fails health checks. Low confidence (0.48), because it can't tell whether the freeze was intended.
- Run 2 diagnosis: The shop-web application intentionally blocked its event loop after a freeze request, so it stopped responding to health checks despite the container remaining running. The failure is in the application behavior, not an apparent host resource or database outage.
  - Judge: Right, with high confidence: a blocked event loop, unhealthy but running, no resource or database cause.
- Run 3 diagnosis: The shop-web application's event loop was deliberately blocked after startup, preventing it from responding to health checks and making it unhealthy. The evidence does not establish the precise underlying reason that requested the freeze.
  - Judge: Right: a blocked event loop stops the health checks from being answered. Like runs 1 and 2, it read the freeze as deliberate and proposed no_action instead of a restart, which is #106 (the self-describing freeze log line).

### Round 2 totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hung | 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 of 3 | 0 | 0 | 8.3 | 22,689 | 7,114 | $0.0067 | 74 s |

## What it shows

- **The root cause is right on every fault but one.** 7 of the 8 faults got 3 of 3 in Round 1, and every run had a diagnosis: none failed, timed out or ran out of context. Most passes scored 0.9 to 1.0. bad-deploy scored 0.80 to 0.85: it named the mechanism every time (the new code selects `price_cents`, a column the table lacks, so `/api/items` answers 500 while the health check stays green), but never the version change or the migration that never ran. The logs are its only evidence until the GitHub tools (#37) can show the commit.
- **Round 1 ran on a shop that is never reset, so every scenario could see the previous one's leftovers, and hung is the one that broke.** The scenarios ran in alphabetical order, and `fault.sh ... off` puts the shop back without clearing its logs (shop-db is only stopped and started, never recreated). disk-full ran right after db-down and saw its leftover database DNS errors; it set them aside correctly in all 3 runs. hung ran right after disk-full, and all 3 of its diagnoses leaned on disk-full's leftover upload-space errors (scores 0.30, 0.00, 0.00). On a reset shop (Round 2), hung named the blocked event loop in 3 of 3 (0.90, 1.00, 0.95). **hung: 0 of 3 on the stale shop, 3 of 3 on a reset shop.** The fix belongs in the harness: reset the shop before each run (#104).
- **hung's plan was 0 of 3 in both rounds, and in Round 2 that is the fault itself.** With clean logs, all three runs read shop-web's own log line ("event loop will be blocked") as a deliberate freeze. Each proposed no action, with a code fix for the human, where a restart is the expected plan. A real hang does not announce itself, so this is a demo artefact (#106), as #34's controls also found.
- **The category was right in 14 of 24 runs, and most misses are the taxonomy, not the diagnosis.** The Diagnose prompt names the seven categories but defines none of them. bad-deploy came out `dependency` in 2 of 3 runs (expected `code`). oom/2 came out `config`, a setting past a limit (expected `resource`). port-conflict came out `config` in 3 of 3 (expected `network`), and bad-env/2 `network` (expected `config`). Each is a defensible reading of an undefined word; the other three misses are hung's stale-log runs. #108 defines the categories in the prompt and lines the scenarios up with them.
- **port-conflict/1 and /3 proposed `docker.stop shop-canary`, then `docker.start shop-proxy`.** The diagnosis was right, and the plan aims at the right thing: free port 8080 and bring the proxy back. But shop-canary is a container outside the incident's service, a plain `docker run` outside the `shop` project, and stopping someone else's workload to fix this one is the human's call. Per [the demo walkthrough](../demo.md#port-conflict), it would not even finish the job on Docker 29: a start of shop-proxy after its failed start comes back without port 8080 published, and the expected fix is a recreate, which Bleep has no tool for. The plan still goes to a human for approval, and Verify would have caught the unpublished port. The harness scores it as an unnecessary action, as the scenario's `must_not` says, and that score stands. Run 2 proposed no action, with instructions for the human.
- **db-down/2 proposed a restart of shop-web after starting shop-db.** It is an extra step: shop-web reconnects once its database is back, and the scenario forbids `docker.restart`.
- **It is cheap and quick.** A run cost $0.0035 on average and used 7.3 tool calls and 40,509 tokens. The largest single prompt was 19,830 tokens (bad-deploy/2), well within the model's context. The incident opened 15 to 60 s after the fault, and the diagnosis came 71 s after it on average. A batch of 8 runs with 30 s between them took 14 to 15 minutes, against 8 to 14 minutes per run on the local Qwen.

## Decided along the way

- **The model under test is `openai/gpt-6-luna`, not the local Qwen**, after Round 0 (Danny's ruling). It is Bleep's default, so this is the baseline people get, and the local model has its own task (#35).
- **The root cause is graded by hand, not by a judge model.** The default judge, `openai/gpt-6-luna`, is the model under test, and the harness refuses that. Claude (Opus 5.5) graded each run against the scenario's expected root cause, with a note per grade. The harness gained a `manual` judge and `python -m evals.report --grades` for this ([design](../design/evals.md)). The GEval judge stays available.
- **Each scenario has 400 s**, down from 900 s, after the timing-check run took 138 s.
- **The suite ran in three batches of one run per scenario**, appended to one raw file with `--append`, so the shared demo shop could alternate with #34 under one lock. Each batch rewrote the same report.
- **Round 2 reset the shop by hand**, with `docker compose ... up -d --force-recreate` before each run, so that hung could be measured without leftovers. The harness does not do this yet (#104).

## Limits

- **Every Round 1 scenario ran on the previous scenario's leftover logs** (#104). hung is the clear case. The other passes are as measured, and a leftover may have helped or hindered them in ways the grades cannot show. Only hung was re-run on a reset shop.
- **The hung fault describes itself in the logs** (#106), so its plan result says more about the demo than about Bleep.
- **bad-deploy's only evidence is the logs.** The plan's GitHub fixture, a commit touching `/api/items`, waits for the GitHub tools (#37).
- **3 runs per scenario is a small sample.** A difference of one run, such as port-conflict acting in 2 of 3, is within what the model does from one run to the next at temperature 0.
- **One grader graded by hand.** Every grade and its note are in the grades files, so each one can be checked against the raw diagnosis.
- **The runs stop at Propose.** Execute, Verify and Report are not measured here.

## Reproduce

From `backend/`, with the demo shop up (`docker compose -f demo/compose.yaml up -d`) and Bleep's backend stopped:

```bash
# Round 1: one run per scenario, then two more batches appended to the same raw file
uv run --group evals python -m evals.run --judge manual --repeat 1
uv run --group evals python -m evals.run --judge manual --repeat 1 --append data/evals/scenarios-<started>.jsonl
uv run --group evals python -m evals.run --judge manual --repeat 1 --append data/evals/scenarios-<started>.jsonl

# Round 2: three times, a reset shop (from the repo root), then one hung run; runs 2 and 3
# are appended to run 1's raw file
docker compose -f demo/compose.yaml up -d --force-recreate   # wait until healthy
uv run --group evals python -m evals.run --judge manual --only hung --repeat 1
docker compose -f demo/compose.yaml up -d --force-recreate
uv run --group evals python -m evals.run --judge manual --only hung --repeat 1 --append data/evals/scenarios-<round 2 start>.jsonl
docker compose -f demo/compose.yaml up -d --force-recreate
uv run --group evals python -m evals.run --judge manual --only hung --repeat 1 --append data/evals/scenarios-<round 2 start>.jsonl

# Grade by hand, then render a round's tables with its grades
uv run --group evals python -m evals.report ../docs/evals/data/scenarios-20261103-124139.jsonl \
  --grades ../docs/evals/data/scenarios-20261103-124139.grades.jsonl --grader "Claude (Opus 5.5)" --out round1.md
```

The Round 1 and Round 2 tables above are that command's output for each raw file, with the headings one level down; the rest of this page is written by hand.
