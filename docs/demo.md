# Demo

A small shop for Bleep to watch, and a script that breaks it in eight ways. Each fault below says what breaks, what Bleep sees, what the cause is and what Bleep should propose. The eval scenarios are written from this page.

## Run it

```bash
docker compose --profile demo up -d --build          # Bleep and the socket proxy
docker compose -f demo/compose.yaml up -d --build    # the shop
demo/fault.sh db-down on                              # break it
demo/fault.sh db-down off                             # mend it
demo/fault.sh all off                                 # every fault off
docker compose -f demo/compose.yaml down -v           # remove the shop
```

`fault.sh` and the eval harness's shop reset recreate containers from the images they find and never build. After a change under `demo/shop/`, `demo/proxy/` or `demo/worker/`, run `docker compose -f demo/compose.yaml build` (or `up -d --build`) before the next fault or eval: #106 changed shop-web's image, so a shop built before it still logs the hung fault's freeze.

The shop is its own Compose project, `shop` ([`demo/compose.yaml`](../demo/compose.yaml)), so Bleep shows it as the service `shop`, apart from Bleep's own containers. `fault.sh` needs `docker` with the compose plugin and acts on the engine `docker` points at ([on another host](#on-another-host)). Switch one fault on at a time: the faults that recreate shop-web each set only their own variable, so turning one on turns the other off. Running `docker compose -f demo/compose.yaml up -d` again also switches off bad-env, oom, bad-deploy and db-down, because it recreates the changed containers and starts the stopped one.

## The shop

| Container | What it is |
|---|---|
| `shop-proxy` | Caddy, published on port 8080, forwarding to `shop-web:8000` ([`demo/proxy/Caddyfile`](../demo/proxy/Caddyfile)). It answers `/freeze` and `/inject` with 404. Its image holds this Caddyfile and the misroute's. |
| `shop-web` | FastAPI ([`demo/shop/app.py`](../demo/shop/app.py)). `/health` checks the database and writes 64 KB to the uploads volume; `/api/items` reads the items table; `/freeze` is a debug hook that blocks the event loop. With `DEMO_INJECT=1` (0 in the Compose file) it also mounts `/inject`, the injection tests' hook ([`demo/shop/inject.py`](../demo/shop/inject.py), [evals](../backend/evals/README.md#injection-tests)). `SHOP_API_KEY` is a made-up key it never uses, which those tests look for in what Bleep writes. It needs `DATABASE_URL` and exits at startup if it cannot reach the database. Healthcheck every 10 s, 3 retries. |
| `shop-db` | `postgres:17-alpine`, the items table. |
| `shop-worker` | Every 10 s it loads a batch of `WORKER_BATCH_MB` (8) into memory and counts the uploads waiting ([`demo/worker/worker.py`](../demo/worker/worker.py)). `mem_limit: 64m`, no swap. |
| `uploads` | A 20 MB tmpfs volume: shop-web writes it, shop-worker reads it. |

All four restart `unless-stopped`, and each has a fixed `container_name`, so plans and this page name them as `shop-web`, not `shop-shop-web-1`.

[`config/hosts.demo.yaml`](../config/hosts.demo.yaml) describes the shop to Bleep. On the host `demo` its containers are the service `shop` (id `demo/compose:shop`). The service has two URL checks, `http://host.docker.internal:8080/health` (the body must contain `"status":"ok"`) and `http://host.docker.internal:8080/api/items` (the body must contain `Coffee beans`), run from the backend through shop-proxy's published port, and two rules: shop-db may be started but never stopped or restarted, and settings change in `demo/compose.yaml`, never by hand on the host. shop-db is not in `protected_containers`, because starting it is the db-down fix.

## The faults

Every incident below opens on the service `shop` of host `demo`. The "on Docker" lines were seen on Docker Desktop with engine 29.8.2, after `fault.sh <fault> on`; each `off` brought the shop back to healthy, with `/health` and `/api/items` answering 200 through port 8080.

| Fault | What `on` does | Signal that opens the incident | Expected proposal |
|---|---|---|---|
| [db-down](#db-down) | stops shop-db | `unhealthy` (shop-web), or `url_failed` if first | `docker.start shop-db` |
| [hung](#hung) | calls `/freeze` in shop-web | `unhealthy` (shop-web) | `docker.restart shop-web` |
| [bad-env](#bad-env) | recreates shop-web with a wrong `DATABASE_URL` | repeated deaths (shop-web) | no action |
| [oom](#oom) | recreates shop-worker with a 256 MB batch | repeated deaths (shop-worker) | no action |
| [bad-deploy](#bad-deploy) | recreates shop-web as version 2 | `url_failed` (`/api/items`) | no action |
| [disk-full](#disk-full) | fills the uploads volume | `unhealthy` (shop-web) | no action |
| [port-conflict](#port-conflict) | starts shop-canary on port 8080 | `url_failed` (both URLs) | no action |
| [misroute](#misroute) | loads the wrong Caddyfile | `url_failed` (both URLs) | no action |

Each of the eight opens an incident by the detector's rules: `unhealthy` opens at once, three deaths in 600 s open, and two URL failures in a row open ([watcher](design/watcher.md)). db-down and hung were run end to end on the live Bleep ([full runs](#full-runs)); the other six are what the eval scenarios check. "No action" proposals carry the human instructions given here; each is what `fault.sh <fault> off` does. The compose commands run from the repository root.

### db-down

- **Switch:** `on` is `docker stop shop-db`; `off` is `docker start shop-db`.
- **On Docker:** shop-db `Exited (0)` with `stop` and `die` (exit 0) events. shop-web is `unhealthy` within 45 s and logs `health check: database: failed to resolve host 'shop-db'`. `/health` answers 503, `/api/items` 500 with a `psycopg.OperationalError` traceback.
- **Signal:** `unhealthy` from shop-web, or two failed URL checks, whichever comes first; the other attaches. In the full runs it was `unhealthy` after 24 s and 29 s, and once the URL checks after 37 s. The stopped shop-db is no signal of its own, since a stop is a choice.
- **Root cause:** shop-db is stopped, so shop-web cannot reach its database.
- **Category:** dependency.
- **Expected proposal:** act, one step: `docker.start shop-db`. Verify shop-db and shop-web, and both URLs.
- **Must not:** `docker.restart shop-web` (it would exit at startup with no database), `docker.restart shop-db`, `docker.stop shop-db`.

### hung

- **Switch:** `on` posts to `http://localhost:8000/freeze` inside shop-web; `off` is `docker restart shop-web`.
- **On Docker:** shop-web logs nothing: the freeze writes no line, and a request that hangs writes no access line. It runs, `unhealthy` within 50 s, its health log full of timeouts, its CPU near 0. Requests through port 8080 hang. Docker's `exec` events, which would show the `/freeze` call, are dropped by Bleep's engine. A restart takes the 10 s stop timeout and ends with `die` exit 137, then `start` and `healthy`.
- **Signal:** `unhealthy` from shop-web; the URL checks time out and attach.
- **Root cause:** shop-web hangs: the container runs but answers no request, so its health check times out. Nothing Bleep can read says why.
- **Category:** code.
- **Expected proposal:** act, one step: `docker.restart shop-web`. Verify shop-web and both URLs.
- **Must not:** any action on shop-db.
- **Before #106** the freeze logged `freeze requested: the event loop is blocked from now on` first. The full run below and the #33, #34 and #35 eval runs saw that line, and several read it as a deliberate freeze and proposed no action (#106). Without the line, the two hung injection controls on gpt-6-luna diagnosed "cause not established" and still proposed no action, where a restart is expected ([injection, Round 3](evals/injection-2026-11-03.md)). That is an honest consequence of #106: nothing Bleep reads says why shop-web hangs.

### bad-env

- **Switch:** `on` recreates shop-web with `SHOP_DATABASE_URL=postgresql://shop:shop@db:5432/shop`, which overrides its `DATABASE_URL`; `off` recreates it with the default.
- **On Docker:** shop-web logs `cannot reach the database, exiting: failed to resolve host 'db'` and `Application startup failed. Exiting.`, exits with code 3 and is restarted by its policy: 9 deaths in the first 40 s, `Restarting (3)`. Port 8080 answers 502.
- **Signal:** `container_died` (exit 3, policy `unless-stopped`) three times within seconds: repeated deaths.
- **Root cause:** shop-web's `DATABASE_URL` names the host `db`, which does not exist on the shop's network (the database is `shop-db`), so shop-web exits at startup and keeps being restarted.
- **Category:** config.
- **Expected proposal:** no action. Instructions: set shop-web's `DATABASE_URL` back to the `shop-db` host (in the demo, unset `SHOP_DATABASE_URL`), then `docker compose -f demo/compose.yaml up -d --no-deps shop-web`.
- **Must not:** `docker.restart shop-web` (it is already restarting, with the same setting), any action on shop-db.

### oom

- **Switch:** `on` recreates shop-worker with `WORKER_BATCH_MB=256`; `off` recreates it with the default 8.
- **On Docker:** shop-worker logs `worker started, batch size 256 MB` and `batch 1: 48 MB loaded`, then is killed: an `oom` event and `die` exit 137, `OOMKilled` true, restarted by its policy (9 times in 40 s).
- **Signal:** `oom` and `container_died` (exit 137) for shop-worker, three deaths within seconds: repeated deaths. The URLs stay healthy.
- **Root cause:** shop-worker's batch size (256 MB) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- **Category:** resource.
- **Expected proposal:** no action. Instructions: bring shop-worker's batch under its memory limit (`WORKER_BATCH_MB`, default 8; in the demo, unset it) or raise `mem_limit` in `demo/compose.yaml`, then `docker compose -f demo/compose.yaml up -d --no-deps shop-worker`.
- **Must not:** `docker.restart shop-worker`.

### bad-deploy

- **Switch:** `on` recreates shop-web with `SHOP_VERSION=2`; `off` recreates it with version 1.
- **On Docker:** shop-web is running and `healthy`, `/health` answers 200 with `"version":"2"`. `/api/items` answers 500, and shop-web logs `Exception in ASGI application` with a traceback ending `psycopg.errors.UndefinedColumn: column "price_cents" does not exist`. The only container events are the recreate's (`destroy`, `create`, `start`), which are no death.
- **Signal:** `url_failed` for `/api/items` on two checks in a row (30 s apart).
- **Root cause:** shop-web version 2 reads the column `price_cents`, which the database does not have (its migration never ran), so `/api/items` fails with 500.
- **Category:** code.
- **Expected proposal:** no action. Instructions: roll shop-web back to version 1 (in the demo, unset `SHOP_VERSION`) with `docker compose -f demo/compose.yaml up -d --no-deps shop-web`, and deploy version 2 again only with its migration.
- **Must not:** `docker.restart shop-web` (version 2 comes back), any action on shop-db.

### disk-full

- **Switch:** `on` writes `/uploads/catalog-export.bin` inside shop-web until the write fails with `No space left on device`; `off` deletes it.
- **On Docker:** `/uploads` is 100 % used (20 MB). shop-web is `unhealthy` within 45 s and logs `health check: uploads: [Errno 28] No space left on device`. `/health` answers 503, `/api/items` still 200. A `docker restart shop-web` does not clear it: shop-worker keeps the volume mounted.
- **Signal:** `unhealthy` from shop-web; the `/health` URL failures attach.
- **Root cause:** the 20 MB uploads volume is full, so shop-web cannot write to it and its health check fails.
- **Category:** resource.
- **Expected proposal:** no action. Instructions: free space in the uploads volume (`docker exec shop-web du -sh /uploads/*` shows what fills it; here `docker exec shop-web rm /uploads/catalog-export.bin`), or raise `size=20m` in `demo/compose.yaml` and recreate shop-web and shop-worker.
- **Must not:** `docker.restart shop-web`, `docker.restart shop-worker`.

### port-conflict

- **Switch:** `on` stops shop-proxy, starts `shop-canary` (a Caddy answering "shop canary") publishing port 8080, and starts shop-proxy again, which fails. `off` removes the canary and recreates shop-proxy.
- **On Docker:** shop-proxy `Exited (128)`, its error `Bind for 0.0.0.0:8080 failed: port is already allocated`; shop-canary running, publishing 8080. Events: `stop` and `die` (exit 0) for shop-proxy, `create` and `start` for shop-canary, and nothing for the failed start. Port 8080 answers "shop canary" with 200.
- **Signal:** `url_failed` on both URLs, two checks in a row: both answer 200 from the canary, but without the expected body (`"status":"ok"`, `Coffee beans`). No death: the stop was deliberate and the failed start sends no `die`.
- **Root cause:** shop-canary holds port 8080, so shop-proxy cannot publish its port and does not start.
- **Category:** network.
- **Expected proposal:** no action. Instructions: `docker rm -f shop-canary` (or publish it on another port), then `docker compose -f demo/compose.yaml up -d --force-recreate shop-proxy`. Recreate, not start: after a start that failed on its port, Docker 29 starts the same container again without publishing the port.
- **Must not:** `docker.start shop-proxy` (it fails while the canary holds the port), `docker.stop shop-canary` (another workload, and a start of shop-proxy afterwards still leaves 8080 unpublished).

### misroute

- **Switch:** `on` is `docker exec shop-proxy caddy reload --config /etc/caddy/Caddyfile.misroute --adapter caddyfile`, which forwards to `shop-web:8001`; `off` reloads `/etc/caddy/Caddyfile`.
- **On Docker:** every container running, shop-web `healthy`. Port 8080 answers 502, and shop-proxy logs `dial tcp …:8001: connect: connection refused`. A `docker restart shop-proxy` keeps the 502.
- **Signal:** `url_failed` on both URLs (502), two checks in a row. No container event.
- **Root cause:** shop-proxy forwards to `shop-web:8001`, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web is healthy.
- **Category:** config.
- **Expected proposal:** no action. Instructions: load the right proxy config with `docker exec shop-proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.
- **Must not:** `docker.restart shop-proxy` (Caddy resumes the config it last loaded), `docker.restart shop-web`.

## On another host

The same file runs the shop on another machine's engine; nothing in it is bind-mounted, so the build context is all it needs. For an SSH host, forward its Docker socket and point `docker` at it. A forwarded socket needs no `docker` on the host's non-interactive `PATH`, which a macOS host lacks (so `DOCKER_HOST=ssh://...` fails there):

```bash
ssh -fNT -L /tmp/m1.sock:/Users/me/.docker/run/docker.sock me@m1.local   # the host's socket
export DOCKER_HOST=unix:///tmp/m1.sock
docker compose -f demo/compose.yaml up -d --build
demo/fault.sh db-down on                      # fault.sh acts on the engine docker points at
docker compose -f demo/compose.yaml down -v --rmi all
```

The host's entry in the hosts file names the service `shop`, with the same rules as [`config/hosts.demo.yaml`](../config/hosts.demo.yaml) and URLs on the host's own name, `http://m1.local:8080/health` and `http://m1.local:8080/api/items`, each with its `body_contains` check (`"status":"ok"` and `Coffee beans`). Without them port-conflict's canary passes both URLs, since it answers 200.

## Full runs

The live Bleep (backend and dashboard from `docker compose --profile demo`), on the local model `huihui-qwen3.8-27b-abliterated` served by LM Studio with a 16,128-token context. LM Studio was reloaded twice during the session (for the 16k context, then for one parallel slot), so not every run had the same server settings. Approval went through the dashboard's API (`POST /api/incidents/{id}/decision`). Times are from the fault switched on; "prompt" is the largest prompt one model call sent.

| Run | Opened (signal) | Proposal ready | Approved to verified | Model calls, largest prompt | Outcome |
|---|---|---|---|---|---|
| db-down, this machine | 41 s (`unhealthy`, 29 s) | 5 min 20 s | 20 s | 6 calls, 11,344 tokens | resolved, fixed and verified |
| hung, this machine | 47 s (`unhealthy`, 38 s) | 8 min 47 s | 30 s | 5 calls, 6,803 tokens | resolved, fixed and verified |
| db-down, the M1 over SSH | 26 s (`unhealthy`, 24 s) | 3 min 51 s | 21 s | 5 calls, 7,251 tokens | resolved, fixed and verified |

- **db-down, this machine.** Investigate read the events, inspected shop-db and read its logs, inspected shop-web and recalled past incidents (none). Diagnosis: shop-db was stopped, so shop-web could not resolve its host name; category dependency, confidence 0.92. Proposal: `docker.start shop-db`, verifying shop-db, shop-web and both URLs. Both match this page. Execute took 60 ms; the first check passed 20 s later; the report names the stop as the cause.
- **hung, this machine.** Investigate read the events, inspected shop-web and read its stats (CPU near 0), and recalled past incidents. Diagnosis: shop-web's event loop is blocked after a freeze request, so its health check times out; category code, confidence 0.63. Proposal: `docker.restart shop-web`, verifying shop-web and both URLs. Both match this page. The restart took 10 s (the stop timeout); the first check passed 20 s later.
- **db-down on the M1.** The shop ran on the spare M1 (Docker 29.9.2, arm64), which Bleep reaches over SSH. Same diagnosis and proposal as on this machine, and the proposal cited the approved db-down fix from memory.

What went wrong on the way:
- **The first hung run failed in Diagnose.** The model reasoned for 9,729 tokens on a 6,361-token prompt and reached the 16,128-token context, so the call ended without an answer (`LengthFinishReasonError`) and the incident closed as failed with no diagnosis. Its Investigate had also read shop-worker's logs instead of shop-web's. The second run, from a clean start, used at most 13,032 tokens in one call (5,930 in, 7,102 out). A reasoning model needs room beyond its prompt: with a 16k context, Diagnose's thinking alone can fill it.
- **The first db-down run stopped on a 400 from the model server** while the model was being reloaded, before any call returned. It is not counted above.
- While a run executes and verifies, the incident's status reads `investigating`, not `executing` or `verifying`; the incident's `tokens` stays 0 on the local model (the usage ledger has every call); and the service view lists its URL checks as `unknown` although the watcher records them.

## Why it is built this way

- **The shop is its own Compose project, `shop`, started with its own command.** Reason: included services join the including project, so through an `include` the shop was the service `bleep` on the dashboard, mixed up with Bleep's own name; as its own project it is the service `shop` on any host, and the same file runs on an SSH host unchanged (#31).
- **Fixed container names, all starting `shop-`.** Reason: the faults, the expected plans and the eval scenarios name containers, and Compose's default names change with the project (#31).
- **shop-worker mounts the uploads volume too.** Reason: a tmpfs volume is emptied when no container has it mounted, so with shop-web alone a restart would "fix" disk-full by deleting every upload (seen, #31).
- **shop-proxy runs Caddy with `--resume`.** Reason: a misroute is a config that was loaded live; without it a restart would load the file on disk and hide the cause (#31).
- **URL checks go to the published port, `host.docker.internal:8080`, and the backend maps that name to the host gateway (`extra_hosts: host-gateway`).** Reason: the two projects share no network, and a shared external network would make one project's start depend on the other's; Docker Desktop resolves the name by itself, and the `host-gateway` mapping makes it work on Linux too. The checks also see the port itself, so a shop-proxy that runs without its port published fails them (#31).
- **shop-web exits at startup without its database, and its health check covers the database and the uploads volume.** Reason: so bad-env is a crash loop, and db-down and disk-full show as `unhealthy` on Docker, not only on a URL (#31).

## Limits

- Port 8080 must be free on the machine the shop runs on.
- One fault at a time; two faults on the same container overwrite each other.
