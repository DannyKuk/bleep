# Scenario evals, 2026-11-11

> `openai/gpt-6-luna` diagnosed and proposed for 8 demo faults, 1 run each; DeepEval's GEval judged the root causes on the Claude Code CLI, and the same 8 were then graded by hand. The root cause passed in 7 of 8 runs and the plan was exact in 7 of 8. GEval and the hand grades agree on pass or fail in 8 of 8 runs, and their scores differ by 0.05 on average (at most 0.2). The batch cost $0.0205 on OpenRouter (#468).

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | `claude-code` through DeepEval GEval: the Claude Code CLI with the model its settings name, `opus` (Claude Opus 5.5 on this date), on the owner's plan; a root cause passes at 0.7 or more |
| Graded by hand | Claude (Opus 5.5), against each scenario's expected root cause, after GEval; every grade and its note in [`scenarios-20261111-153102.grades.jsonl`](data/scenarios-20261111-153102.grades.jsonl). These grades are compared with GEval's [below](#geval-against-grading-by-hand); the tables keep GEval's scores |
| Scenarios | bad-deploy, bad-env, db-down, disk-full, hung, misroute, oom, port-conflict, 1 run each |
| When | 2026-11-11 15:31 UTC, 15 min |
| Commit | `5b62434a` |
| Raw data | [`scenarios-20261111-153102.jsonl`](data/scenarios-20261111-153102.jsonl) |
| Reproduce | `export EVAL_JUDGE_MODEL=claude-code:opus`, then `uv run --group evals python -m evals.run --only bad-deploy --repeat 1` and, for each of bad-env, db-down, disk-full, hung, misroute, oom and port-conflict, `uv run --group evals python -m evals.run --only <scenario> --repeat 1 --append data/evals/<that batch>.jsonl`, the raw file the first command wrote (in `backend/`); `--only` takes one id. The batch ran with `EVAL_JUDGE_MODEL=claude-code`, so the CLI used the model set in its own settings, `opus`; the raw file and info record only `claude-code` |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app, and the linked repo docs `DannyKuk/bleep: demo/shop/docs/deploy.md`, `DannyKuk/bleep: demo/shop/docs/services.md`, read from this checkout.

## bad-deploy

- Opened by the fault `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo/shop", "since_hours": 48})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 300})`, `commit_diff({"sha": "3350063"})`, `knowledge_search({"query": "How are shop database migrations applied, and how is SHOP_VERSION co…)` | 46,536 | 10,761 | $0.0041 | 60 s | 91 s |

- Run 1 diagnosis: The shop-web code is running the version-2 `/api/items` query against the existing `shop-db` schema, which lacks `items.price_cents`; the version-2 migration that adds that column has not been applied to the persistent database.
  - Judge: The diagnosis matches the expected root cause: shop-web version 2 queries items.price_cents, the shop-db schema lacks that column, and the 0002_price_cents.sql migration was never applied to the persistent database. The evidence supports this with the failing query in shop-web and shop-db logs, commit 3350063, and docs listing the existing columns. It does not explicitly say shop-web stays healthy while /api/items returns 500, but it commits to the correct component and reason.

## bad-env

- Opened by the fault `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `DATABASE_URL`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 7 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 30})`, `inspect_container({"container": "shop-worker"})`, `knowledge_search({"query": "What value should DATABASE_URL use for the shop service's database c…)`, `container_logs({"container": "shop-web", "since_minutes": 10, "tail": 80})` | 35,028 | 8,650 | $0.0031 | 15 s | 37 s |

- Run 1 diagnosis: shop-web's DATABASE_URL points to the unresolvable host db, so startup cannot reach the database. The shop deploy docs specify the shop database on shop-db:5432; the current URL's database name and credentials are not fully visible, so only the host mismatch is established.
  - Judge: The diagnosis commits to the same root cause as the expected output: shop-web's DATABASE_URL uses the host 'db', which does not resolve, while the real database is shop-db. It also matches the reason for failure, since shop-web cannot reach the database and exits during startup. The supporting evidence is correct and relevant: logs show the startup exits, inspect_container shows the DATABASE_URL host and a healthy shop-db, and the deploy docs specify shop-db:5432. The config category is fitting. Its caveat that credentials and database name are not fully visible is extra correct detail and does not weaken the main claim. It mentions repeated exits but does not explicitly describe the restart loop, which is a minor omission.
  - Names: the diagnosis names every one, the plan names every one

## db-down

- Opened by the fault `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | docker.start shop-db | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-db", "tail": 120})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-worker", "tail": 100})`, `inspect_container({"container": "shop-proxy"})` | 25,248 | 7,114 | $0.0026 | 30 s | 56 s |

- Run 1 diagnosis: The shop-db dependency was stopped, so shop-web could no longer resolve or connect to its database and became unhealthy. The collected events show shop-db was killed and stopped; the reason it was stopped is not established.
  - Judge: The diagnosis commits to the same root cause as the expected output: the shop-db database container is stopped, so shop-web cannot resolve or connect to it at shop-db:5432 and the API fails (/api/items returning 500). It correctly treats the 500s and the failing health checks as symptoms rather than the cause. The supporting evidence fits: kill, stop and die events for shop-db, exit code 0, the DATABASE_URL pointing at shop-db, and PostgreSQL logging a clean fast shutdown. Saying that the reason for the stop is not established is accurate extra detail and does not weaken the committed diagnosis. The dependency category also fits.

## disk-full

- Opened by the fault `disk-full`
- Expected root cause: The shop's 20 MB uploads volume is full (No space left on device), so shop-web cannot write to it and its health check fails.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | resource | no action | yes | no | 7 | `list_containers`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})`, `container_stats({"container": "shop-web"})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-worker", "since_minutes": 60, "tail": 100})`, `inspect_container({"container": "shop-db"})` | 23,738 | 5,295 | $0.0025 | 30 s | 61 s |

- Run 1 diagnosis: The shop_uploads volume mounted at /uploads ran out of available space, causing shop-web's upload health check to return 503 and mark it unhealthy.
  - Judge: The diagnosis matches the expected root cause: it names the same failing component (the shop_uploads volume mounted at /uploads in shop-web) and the same reason (it ran out of space, with the logs showing 'no space left on device'). It also links this correctly to the upload health check returning 503, rather than treating the unhealthy container as the cause. Categorizing it as a resource issue is correct. It leaves out the volume's 20 MB size limit, but that is a minor detail and does not change the cause.

## hung

- Opened by the fault `hung`
- Expected root cause: shop-web hangs: the container runs but answers no request, so its health check times out.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.20 fail | unknown | no action | no | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 200})`, `recent_events({"minutes": 60})`, `container_stats({"container": "shop-web"})`, `inspect_container({"container": "shop-proxy"})`, `container_logs({"container": "shop-proxy", "since_minutes": 60, "tail": 100})`, `inspect_container({"container": "shop-db"})` | 27,932 | 5,744 | $0.0029 | 45 s | 72 s |

- Run 1 diagnosis: The cause of shop-web's unhealthy status is not established from the collected evidence; its application logged successful /health responses, but the Docker health-check failure and configuration are unavailable.
  - Judge: The expected root cause is that shop-web hangs, running but answering no requests, so its health check times out. The diagnosis commits to no cause and uses the category 'unknown'. It identifies the right component and the symptom (running but unhealthy), but it never mentions a hang or a health-check timeout. It also suggests the app logged successful /health responses, which points away from a hang. This is a non-diagnosis rather than a match.

## misroute

- Opened by the fault `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 4 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})` | 26,924 | 6,741 | $0.0026 | 60 s | 82 s |

- Run 1 diagnosis: shop-proxy loaded /etc/caddy/Caddyfile.misroute, which routes requests to shop-web at port 8001 even though shop-web listens on port 8000. This incorrect proxy upstream port caused the 502 responses.
  - Judge: The diagnosis matches the expected root cause: shop-proxy forwards to shop-web on port 8001 while shop-web listens on 8000, so proxied requests get 502s. It names the right component (shop-proxy's config, the Caddyfile.misroute upstream) and the right reason (wrong upstream port). Its evidence fits: refused connections to 172.23.0.4:8001, and shop-web healthy on 0.0.0.0:8000 serving /health and /api/items. It commits to one cause and adds correct detail, the config file name and the config category.

## oom

- Opened by the fault `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `WORKER_BATCH_MB`, `WORKER_BATCH_MB=8`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `container_stats({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-worker", "since_minutes": 30, "tail": 100})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `knowledge_search({"query": "WORKER_BATCH_MB setting recommended value", "sources": ["repo"]})` | 22,040 | 6,667 | $0.0022 | 15 s | 30 s |

- Run 1 diagnosis: shop-worker is being OOM-killed because WORKER_BATCH_MB is set to 256 MB while its container memory limit is 64 MiB, so each batch exceeds the available limit. The shop deploy docs specify WORKER_BATCH_MB=8.
  - Judge: The diagnosis names the same failing component (shop-worker) and the same reason: WORKER_BATCH_MB=256 exceeds its 64 MiB memory limit, causing OOM kills. It commits to this single cause, classifies it as resource, and backs it with evidence from inspect_container (64 MiB limit, OOMKilled, exit 137) and container_logs (256 MB batch before repeated OOM kills). The note that the deploy docs specify WORKER_BATCH_MB=8 is correct, useful extra detail. It mentions repeated OOM kills, though it does not spell out the restart loop as explicitly as the expected output.
  - Names: the diagnosis names every one, the plan names every one

## port-conflict

- Opened by the fault `port-conflict`
- Expected root cause: shop-canary holds port 8080, so shop-proxy cannot publish its port (port is already allocated) and does not start; the shop's URLs answer the canary instead.
- Expected category `network`, plan no action, never `docker.start`, `docker.stop`, `docker.restart`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | network | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-canary"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 60})`, `container_logs({"container": "shop-canary", "tail": 100})`, `container_stats({"container": "shop-canary"})`, `container_logs({"container": "shop-proxy", "tail": 100})` | 24,266 | 6,371 | $0.0024 | 60 s | 85 s |

- Run 1 diagnosis: The shop-canary container is holding host port 8080, so shop-proxy cannot bind its published port and exits; the shop health URL is consequently served by the canary rather than the shop proxy.
  - Judge: The diagnosis names the same root cause as the expected output: shop-canary holds host port 8080, so shop-proxy cannot publish its port ('port is already allocated') and exits, leaving the canary to answer the shop's URLs. It commits to this single cause rather than hedging. The evidence is correct and specific: the inspect results show the allocation failure and the canary's 8080 mapping. Noting that shop-web and shop-db are healthy is correct extra detail and does not dilute the diagnosis. The 'network' category fits.

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 46,536 | 10,761 | $0.0041 | 91 s |
| bad-env | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 7.0 | 35,028 | 8,650 | $0.0031 | 37 s |
| db-down | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 25,248 | 7,114 | $0.0026 | 56 s |
| disk-full | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 7.0 | 23,738 | 5,295 | $0.0025 | 61 s |
| hung | 1 | 0 of 1 | 0 of 1 | 0 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 27,932 | 5,744 | $0.0029 | 72 s |
| misroute | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 4.0 | 26,924 | 6,741 | $0.0026 | 82 s |
| oom | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 22,040 | 6,667 | $0.0022 | 30 s |
| port-conflict | 1 | 1 of 1 | 1 of 1 | 1 of 1 | 0 of 1 | 0 | 0 | 0 | 8.0 | 24,266 | 6,371 | $0.0024 | 85 s |
| **All** | 8 | 7 of 8 | 7 of 8 | 7 of 8 | 0 of 8 | 0 | 0 | 0 | 7.2 | 28,964 | 10,761 | $0.0224 | 64 s |

## GEval against grading by hand

| Run | GEval score | GEval | By hand | Agree | GEval's reason, in short | Hand note |
|---|---|---|---|---|---|---|
| bad-deploy/1 | 0.90 | pass | 1.00 pass | yes | right component and reason; does not say shop-web stays healthy | names version 2 querying price_cents, which shop-db lacks because the migration was not applied |
| bad-env/1 | 1.00 | pass | 1.00 pass | yes | DATABASE_URL names the unresolvable host db; the restart loop not spelled out | names DATABASE_URL pointing at db where the database is shop-db |
| db-down/1 | 1.00 | pass | 1.00 pass | yes | shop-db stopped, 500s and failing health checks treated as symptoms | names shop-db stopped; says honestly why it stopped is not known |
| disk-full/1 | 0.90 | pass | 1.00 pass | yes | the uploads volume out of space; leaves out its 20 MB size | the same, the size not named |
| hung/1 | 0.20 | fail | 0.00 fail | yes | a non-diagnosis: category unknown, no hang or timeout, logs read as healthy | names no cause and reads the logged /health lines as answers, the opposite of a hang |
| misroute/1 | 1.00 | pass | 1.00 pass | yes | shop-proxy sends to port 8001, shop-web listens on 8000 | the same, with the Caddyfile named |
| oom/1 | 1.00 | pass | 1.00 pass | yes | WORKER_BATCH_MB=256 past the 64 MiB limit | the same |
| port-conflict/1 | 1.00 | pass | 1.00 pass | yes | shop-canary holds 8080, so shop-proxy cannot publish its port | the same |

- Pass or fail agree in 8 of 8 runs. The scores differ by 0.05 on average and by at most 0.2 (hung: 0.20 against 0.00).
- GEval takes points for a missing detail of the expected root cause (shop-web staying healthy, the volume's size, the restart loop); the hand grades did not, as in the earlier batches graded by hand. Neither difference moves a run across 0.7.
- The two are not independent: the judge runs on Claude Opus 5.5 and the hand grades were written by Claude Opus 5.5, after GEval's reasons were in the raw file. So the agreement shows GEval reads these diagnoses as a careful reader does, not that a second, unrelated grader agrees. A judge from another model family, or a human's grades, would test that.
- 8 runs with one miss is too few to say how GEval does near the 0.7 line, where a judge's mistakes matter: no diagnosis here scored between 0.3 and 0.8.

## Cost

- Model under test: $0.0224, from the runs' own counts. The OpenRouter key's usage went from $8.1157 to $8.1362 over the batch ($0.0205; the live backend was stopped, so nothing else used the key then).
- Judge: n/a (billed to a plan, not per call).

## What it shows

- The root cause passed in 7 of 8 runs, and the category was right in 7 of 8.
- The plan was exactly the expected one in 7 of 8 runs; 0 proposed an unnecessary action.
- The diagnosis named everything its fix needs in 2 of 2 runs that score it, and the plan in 2 of 2.
- The largest single prompt was 10,761 tokens (bad-deploy, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
