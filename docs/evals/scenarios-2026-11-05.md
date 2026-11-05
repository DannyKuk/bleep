# Scenario evals, 2026-11-05

> `openai/gpt-6-luna` diagnosed and proposed for 9 demo faults, 3 runs each; Claude (Opus 5.5) graded the root causes by hand. The root cause passed in 22 of 27 runs and the plan was exact in 22 of 27.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | Claude (Opus 5.5), by hand, against each scenario's expected root cause; every grade and its note in [`scenarios-20261105-101353.grades.jsonl`](data/scenarios-20261105-101353.grades.jsonl) |
| Scenarios | bad-deploy, bad-env, db-down, disk-full, hung, misroute-mcp, misroute, oom, port-conflict, 3 runs each |
| When | 2026-11-05 10:13 UTC, 39 min |
| Commit | `782551e` |
| Raw data | [`scenarios-20261105-101353.jsonl`](data/scenarios-20261105-101353.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --judge manual --repeat 3` (in `backend/`) |
| Graded | `uv run --group evals python -m evals.report ../docs/evals/data/scenarios-20261105-101353.jsonl --grades ../docs/evals/data/scenarios-20261105-101353.grades.jsonl --grader 'Claude (Opus 5.5)' --out ../docs/evals/scenarios-2026-11-05.md` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app.

## bad-deploy

- Fault: `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo", "since_hours": 720})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `container_logs({"container": "shop-web", "tail": 200})` | 41,072 | 9,254 | $0.0032 | 60 s | 76 s |
| 2 | 1.00 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": null, "since_hours": 720})`, `recent_events({"minutes": 60})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-web", "tail": 100})`, `container_logs({"container": "shop-db", "tail": 100})` | 40,103 | 8,547 | $0.0031 | 60 s | 74 s |
| 3 | 1.00 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo", "since_hours": 168})`, `recent_events({"minutes": 120})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 100})` | 41,998 | 9,450 | $0.0033 | 60 s | 75 s |

- Run 1 diagnosis: Shop version 2 now queries the price_cents column, but the existing database lacks that column because the version's migration was not applied. The /api/items endpoint consequently returns 500 while health checks succeed.
  - Judge: names version 2 reading price_cents, which the database lacks (migration not run)
- Run 2 diagnosis: shop-web was deployed as version 2, which queries the price_cents column, but the shop database lacks that column because the migration was not applied. This schema mismatch causes /api/items to return 500.
  - Judge: names version 2 reading price_cents, which the database lacks (migration not run)
- Run 3 diagnosis: shop-web is running version 2, which queries the new `price_cents` column, but the existing shop database schema lacks that column because the migration was not applied. This version-to-schema mismatch makes `/api/items` return 500.
  - Judge: names version 2 reading price_cents, which the database lacks (migration not run)

## bad-env

- Fault: `bad-env`
- Expected root cause: shop-web's DATABASE_URL names the host `db`, which does not exist on the shop's network (the database is shop-db), so shop-web cannot reach its database, exits at startup and keeps being restarted.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.50 fail | unknown | no action | yes | no | 9 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-db", "since_minutes": 30, "tail": 100})`, `container_logs({"container": "shop-worker", "since_minutes": 30, "tail": 80})`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})` | 63,129 | 10,619 | $0.0050 | 15 s | 43 s |
| 2 | 1.00 pass | config | no action | yes | no | 8 | `list_containers`, `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-db", "since_minutes": 30, "tail": 100})`, `container_logs({"container": "shop-proxy", "since_minutes": 30, "tail": 100})` | 26,929 | 8,322 | $0.0027 | 15 s | 30 s |
| 3 | 0.50 fail | unknown | no action | yes | no | 5 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 30})`, `knowledge_search({"query": "What does demo/compose.yaml configure for shop-web DATABASE_URL, the…)` | 53,126 | 9,688 | $0.0048 | 15 s | 52 s |

- Run 1 diagnosis: shop-web fails during startup because it cannot resolve the configured database hostname `db`. The evidence does not establish whether the hostname is incorrect or Docker network discovery/aliasing is misconfigured.
  - Judge: names the unresolvable host db but leaves open whether the setting or Docker networking is wrong
- Run 2 diagnosis: shop-web is failing during startup because its database connection URL points to hostname `db`, which does not resolve in the Compose network, while the database container is running as shop-db. This is a misconfigured database address.
  - Judge: names DATABASE_URL pointing at host db while the database runs as shop-db
- Run 3 diagnosis: shop-web exits during startup because it cannot resolve the database hostname `db`. The database is healthy, but the collected evidence does not distinguish a wrong hostname/alias from a container network or DNS issue, so the underlying cause is not yet established.
  - Judge: names the unresolvable host db but says the cause is not established

## db-down

- Fault: `db-down`
- Expected root cause: The database container shop-db is stopped, so shop-web cannot connect to its database and the shop's API fails.
- Expected category `dependency`, plan `docker.start shop-db`, never `docker.restart`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | dependency | docker.start shop-db, docker.restart shop-web | no | yes | 8 | `list_containers`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-db", "tail": 150})`, `recent_events({"minutes": 60})`, `container_logs({"container": "shop-worker", "tail": 100})`, `inspect_container({"container": "shop-worker"})`, `knowledge_search({"query": "What does a Docker container kill event followed by a clean fast shu…)` | 25,389 | 7,778 | $0.0025 | 30 s | 46 s |
| 2 | 1.00 pass | dependency | docker.start shop-db | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-db"})`, `container_logs({"container": "shop-db", "tail": 100})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-worker", "tail": 100})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-web", "since_minutes": 30, "tail": 40})` | 25,241 | 7,747 | $0.0024 | 30 s | 44 s |
| 3 | 1.00 pass | dependency | docker.start shop-db | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-db", "tail": 100})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-worker", "tail": 100})`, `container_logs({"container": "shop-web", "since_minutes": 10, "tail": 35})` | 25,712 | 7,958 | $0.0026 | 30 s | 46 s |

- Run 1 diagnosis: The shop database container stopped cleanly while shop-web still depended on it. With shop-db no longer running, the web service could not resolve the database container name and its health check and item requests failed.
  - Judge: names shop-db stopped, so shop-web cannot reach its database
  - Forbidden steps proposed: docker.restart shop-web
- Run 2 diagnosis: The shop database container is stopped, so Docker no longer resolves shop-db and shop-web's database health checks and item requests fail. The web service is unhealthy because its required database dependency is unavailable.
  - Judge: names shop-db stopped, so shop-web cannot reach its database
- Run 3 diagnosis: The shop database container shop-db stopped cleanly, leaving shop-web unable to resolve its database dependency; health checks and item requests then failed. The collected evidence does not establish what caused the database to stop.
  - Judge: names shop-db stopped, so shop-web cannot reach its database

## disk-full

- Fault: `disk-full`
- Expected root cause: The shop's 20 MB uploads volume is full (No space left on device), so shop-web cannot write to it and its health check fails.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `container_stats({"container": "shop-web"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-worker", "tail": 200})`, `inspect_container({"container": "shop-worker"})`, `container_logs({"container": "shop-db", "since_minutes": 30, "tail": 100})`, `knowledge_search({"query": "shop-worker processes batches of 8 MB and writes to shared uploads v…)` | 38,135 | 7,020 | $0.0037 | 30 s | 61 s |
| 2 | 1.00 pass | resource | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-worker", "tail": 100})`, `container_stats({"container": "shop-worker"})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-proxy", "tail": 50})` | 18,105 | 5,291 | $0.0020 | 30 s | 44 s |
| 3 | 1.00 pass | resource | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-worker"})`, `recent_events({"minutes": 60})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-worker", "tail": 100})`, `container_logs({"container": "shop-web", "tail": 100})`, `knowledge_search({"query": "shop-worker WORKER_BATCH_MB writes uploads volume causing No space l…)` | 18,149 | 5,328 | $0.0020 | 30 s | 43 s |

- Run 1 diagnosis: The uploads storage mounted at /uploads is out of space, causing shop-web's health check to fail with 503. The evidence does not establish whether the exhaustion is limited to the shop_uploads volume or is on the host's backing filesystem.
  - Judge: names the uploads volume out of space (ENOSPC), failing the health check
- Run 2 diagnosis: The shop-web health check failed because its uploads volume ran out of space, producing ENOSPC and HTTP 503 responses. The worker was continuing to process uploads while sharing that volume, but the collected evidence does not establish why the volume filled.
  - Judge: names the uploads volume out of space (ENOSPC), failing the health check
- Run 3 diagnosis: The shop uploads volume ran out of space, causing shop-web's uploads health check to fail with ENOSPC and return 503s.
  - Judge: names the uploads volume out of space (ENOSPC), failing the health check

## hung

- Fault: `hung`
- Expected root cause: shop-web hangs: the container runs but answers no request, so its health check times out.
- Expected category `code`, plan `docker.restart shop-web`, never `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.00 fail | unknown | no action | no | no | 9 | `list_containers`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 60})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-web", "tail": 200})`, `container_logs({"container": "shop-proxy", "tail": 100})`, `container_logs({"container": "shop-db", "tail": 100})`, `knowledge_search({"query": "shop-web becomes unhealthy after starting; health endpoint returns 2…)`, `inspect_container({"container": "shop-web"})` | 34,361 | 6,660 | $0.0029 | 45 s | 64 s |
| 2 | 0.00 fail | unknown | no action | no | no | 9 | `list_containers`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 200})`, `recent_events({"minutes": 60})`, `container_stats({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-proxy"})`, `container_logs({"container": "shop-proxy", "since_minutes": 60, "tail": 100})`, `inspect_container({"container": "shop-web"})` | 37,981 | 7,314 | $0.0037 | 45 s | 77 s |
| 3 | 0.00 fail | unknown | no action | no | no | 10 | `list_containers`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})`, `container_stats({"container": "shop-web"})`, `container_logs({"container": "shop-web", "since_minutes": 15, "tail": 200})`, `container_logs({"container": "shop-proxy", "since_minutes": 15, "tail": 100})`, `knowledge_search({"query": "shop-web healthcheck unhealthy SHOP_DEBUG_HOOK shop version uploads …)`, `knowledge_search({"query": "shop service unhealthy healthcheck debug hook SHOP_DEBUG_HOOK", "sou…)`, `inspect_container({"container": "shop-web"})`, `knowledge_search({"query": "shop service demo/compose.yaml healthcheck command shop-web", "sourc…)` | 40,555 | 6,790 | $0.0037 | 45 s | 76 s |

- Run 1 diagnosis: The cause of shop-web being marked unhealthy is not established. The application logs show successful health requests, but the configured health check and failing probe result are unavailable.
  - Judge: no cause named; reads healthy log lines and misses the hang
- Run 2 diagnosis: The cause of shop-web's unhealthy status cannot be determined from the collected evidence: its application logs show successful health and API responses, but the health-check configuration and probe failure details are absent.
  - Judge: no cause named; reads healthy log lines and misses the hang
- Run 3 diagnosis: The cause of shop-web's unhealthy status is not established: application logs show successful health requests, but the health-check command and its recorded failures are absent. The available evidence does not distinguish a failing probe from an unhealthy status that does not reflect an ongoing application failure.
  - Judge: no cause named; reads healthy log lines and misses the hang

## misroute-mcp

- Fault: `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan `mcp.call shop-proxy (caddy_reload) with {"container": "shop-proxy"}`, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | mcp.call shop-proxy (caddy_reload) | yes | no | 6 | `list_containers`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `mcp_tools`, `mcp_call({"arguments": {"container": "shop-proxy"}, "tool": "caddy_config"})` | 37,141 | 8,031 | $0.0033 | 60 s | 82 s |
| 2 | 1.00 pass | config | mcp.call shop-proxy (caddy_reload) | yes | no | 6 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 15})`, `mcp_tools`, `mcp_call({"arguments": {"container": "shop-proxy"}, "tool": "caddy_config"})` | 45,001 | 8,029 | $0.0034 | 60 s | 92 s |
| 3 | 1.00 pass | config | mcp.call shop-proxy (caddy_reload) | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})`, `mcp_tools`, `mcp_call({"arguments": {"container": "shop-proxy"}, "tool": "caddy_config"})`, `knowledge_search({"query": "SHOP_DEBUG_HOOK Caddyfile.misroute route /inject /freeze shop incide…)`, `container_logs({"container": "shop-web", "tail": 200})` | 44,371 | 7,986 | $0.0029 | 60 s | 79 s |

- Run 1 diagnosis: The proxy’s live Caddy configuration routed requests to shop-web on port 8001, but the web service listens on port 8000. That incorrect upstream caused the proxy’s 502 responses.
  - Judge: names the live proxy route to shop-web:8001 while shop-web listens on 8000
- Run 2 diagnosis: The proxy’s live Caddy configuration routes requests to shop-web on port 8001, but the web service listens on port 8000. This incorrect upstream setting causes the proxy’s connection refusals and 502 responses; the Caddyfile on disk shows the correct port, so the misroute is in the loaded configuration.
  - Judge: names the live proxy route to shop-web:8001 while shop-web listens on 8000
- Run 3 diagnosis: The running Caddy configuration was loaded with shop-web:8001 as its reverse-proxy upstream, but shop-web listens on port 8000. Requests through the proxy were refused and returned 502; the on-disk Caddyfile has the correct port, so the live configuration was stale or misloaded.
  - Judge: names the live proxy route to shop-web:8001 while shop-web listens on 8000

## misroute

- Fault: `misroute`
- Expected root cause: shop-proxy forwards to shop-web:8001, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web itself is healthy.
- Expected category `config`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | config | no action | yes | no | 5 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})`, `inspect_container({"container": "shop-db"})` | 37,757 | 8,764 | $0.0038 | 60 s | 92 s |
| 2 | 1.00 pass | config | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 60})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 200})`, `container_logs({"container": "shop-worker", "since_minutes": 60, "tail": 200})`, `container_logs({"container": "shop-proxy", "since_minutes": 60, "tail": 100})`, `knowledge_search({"query": "SHOP_DEBUG_HOOK Caddyfile.misroute proxy target port 8001", "sources…)` | 37,502 | 9,816 | $0.0032 | 60 s | 75 s |
| 3 | 1.00 pass | config | no action | yes | no | 4 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-web"})`, `recent_events({"minutes": 30})` | 27,375 | 7,122 | $0.0030 | 60 s | 77 s |

- Run 1 diagnosis: shop-proxy loaded a misrouted Caddy configuration that sends requests to 172.23.0.4:8001, while shop-web is listening on port 8000. The proxy's upstream address/port is therefore misconfigured, producing 502 responses.
  - Judge: names the proxy forwarding to port 8001 while shop-web listens on 8000
- Run 2 diagnosis: The proxy was reloaded with a misrouted Caddy configuration that sends requests to shop-web at 172.23.0.4:8001, while the web service listens on port 8000. The resulting refused upstream connections caused the 502 responses.
  - Judge: names the proxy forwarding to port 8001 while shop-web listens on 8000
- Run 3 diagnosis: The proxy loaded a misrouted Caddy configuration that directs requests to 172.23.0.4:8001, where no service is listening. The web service is healthy on port 8000, so the proxy configuration caused the 502 failures.
  - Judge: names the proxy forwarding to port 8001 while shop-web listens on 8000

## oom

- Fault: `oom`
- Expected root cause: shop-worker's batch size (WORKER_BATCH_MB=256) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- Expected category `resource`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | resource | no action | yes | no | 5 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `recent_events({"minutes": 20})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})` | 20,005 | 6,030 | $0.0023 | 15 s | 32 s |
| 2 | 1.00 pass | resource | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-worker", "since_minutes": 30, "tail": 100})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `inspect_container({"container": "shop-proxy"})`, `knowledge_search({"query": "What does Docker OOMKilled true with exit code 137 and a 64 MiB cont…)` | 21,753 | 7,194 | $0.0023 | 15 s | 28 s |
| 3 | 1.00 pass | resource | no action | yes | no | 5 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `container_stats({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-worker", "since_minutes": 30, "tail": 100})` | 20,593 | 6,864 | $0.0023 | 15 s | 37 s |

- Run 1 diagnosis: shop-worker is configured to process 256 MB batches but is limited to 64 MiB of memory, so it is repeatedly OOM-killed and restarted. The oversized batch relative to its container memory limit caused the service failure.
  - Judge: names the 256 MB batch past the 64 MiB limit, OOM-killed in a loop
- Run 2 diagnosis: shop-worker is repeatedly OOM-killed because its 256 MB batch workload exceeds its 64 MiB container memory limit. The worker then enters a restart loop.
  - Judge: names the 256 MB batch past the 64 MiB limit, OOM-killed in a loop
- Run 3 diagnosis: shop-worker is repeatedly OOM-killed because its 256 MB batch exceeds its 64 MiB container memory limit. The worker's restart policy causes the same failure to recur; shop-web, shop-proxy, and shop-db are running.
  - Judge: names the 256 MB batch past the 64 MiB limit, OOM-killed in a loop

## port-conflict

- Fault: `port-conflict`
- Expected root cause: shop-canary holds port 8080, so shop-proxy cannot publish its port (port is already allocated) and does not start; the shop's URLs answer the canary instead.
- Expected category `network`, plan no action, never `docker.start`, `docker.stop`, `docker.restart`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | network | docker.stop shop-canary, docker.start shop-proxy | no | yes | 5 | `list_containers`, `recent_events({"minutes": 30})`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-canary"})`, `inspect_container({"container": "shop-web"})` | 26,540 | 7,804 | $0.0030 | 60 s | 86 s |
| 2 | 1.00 pass | network | no action | yes | no | 5 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-canary"})`, `recent_events({"minutes": 30})`, `container_logs({"container": "shop-canary", "since_minutes": 10, "tail": 100})` | 33,035 | 7,771 | $0.0032 | 60 s | 87 s |
| 3 | 1.00 pass | network | no action | yes | no | 4 | `list_containers`, `inspect_container({"container": "shop-proxy"})`, `inspect_container({"container": "shop-canary"})`, `recent_events({"minutes": 30})` | 24,998 | 6,684 | $0.0028 | 60 s | 87 s |

- Run 1 diagnosis: The shop-canary container took host port 8080, so shop-proxy could not bind that port and exited. Requests to the shop URL were therefore answered by the canary instead of the shop proxy.
  - Judge: names shop-canary holding port 8080 so shop-proxy cannot bind it
  - Forbidden steps proposed: docker.stop shop-canary, docker.start shop-proxy
- Run 2 diagnosis: The shop proxy could not bind host port 8080 because shop-canary already owned it. The health checks therefore reached the canary's static server instead of the proxy and got an unexpected health response.
  - Judge: names shop-canary holding port 8080 so shop-proxy cannot bind it
- Run 3 diagnosis: The shop-canary container took host port 8080, so shop-proxy could not bind that port and exited. Requests therefore reached the canary’s Caddy server instead of the intended shop proxy, returning unexpected content.
  - Judge: names shop-canary holding port 8080 so shop-proxy cannot bind it

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 8.0 | 41,058 | 9,450 | $0.0095 | 75 s |
| bad-env | 3 | 1 of 3 | 1 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 7.3 | 47,728 | 10,619 | $0.0125 | 42 s |
| db-down | 3 | 3 of 3 | 3 of 3 | 2 of 3 | 1 of 3 | 0 | 0 | 0 | 8.0 | 25,447 | 7,958 | $0.0075 | 45 s |
| disk-full | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 8.0 | 24,796 | 7,020 | $0.0077 | 49 s |
| hung | 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 | 0 | 0 | 9.3 | 37,632 | 7,314 | $0.0103 | 72 s |
| misroute-mcp | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 6.7 | 42,171 | 8,031 | $0.0097 | 84 s |
| misroute | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 5.7 | 34,211 | 9,816 | $0.0100 | 81 s |
| oom | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 6.0 | 20,784 | 7,194 | $0.0070 | 32 s |
| port-conflict | 3 | 3 of 3 | 3 of 3 | 2 of 3 | 1 of 3 | 0 | 0 | 0 | 4.7 | 28,191 | 7,804 | $0.0090 | 87 s |
| **All** | 27 | 22 of 27 | 22 of 27 | 22 of 27 | 2 of 27 | 0 | 0 | 0 | 7.1 | 33,558 | 10,619 | $0.0833 | 63 s |

## Cost

- Model under test: $0.0833.
- Judge: none, graded by hand.

## What it shows

- The root cause passed in 22 of 27 runs, and the category was right in 22 of 27.
- The plan was exactly the expected one in 22 of 27 runs; 2 proposed an unnecessary action.
- The largest single prompt was 10,619 tokens (bad-env, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
