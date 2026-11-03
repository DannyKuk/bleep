# Demo

A small shop for Bleep to watch, and a script that breaks it in eight ways. Each fault below says what breaks, what Bleep sees, what the cause is and what Bleep should propose. The eval scenarios are written from this page.

## Run it

```bash
docker compose --profile demo up -d --build   # Bleep, the socket proxy and the shop
demo/fault.sh db-down on                       # break it
demo/fault.sh db-down off                      # mend it
demo/fault.sh all off                          # every fault off
```

`fault.sh` runs on the machine the shop runs on and needs `docker` with the compose plugin. Switch one fault on at a time: the faults that recreate shop-web each set only their own variable, so turning one on turns the other off. Running `docker compose --profile demo up -d` again also switches off bad-env, oom, bad-deploy and db-down, because it recreates the changed containers and starts the stopped one.

The `demo` profile is the only switch: [`docker-compose.yaml`](../docker-compose.yaml) includes [`demo/compose.yaml`](../demo/compose.yaml), and every shop service is in the `demo` profile beside the socket proxy. Without the profile neither starts.

## The shop

| Container | What it is |
|---|---|
| `shop-proxy` | Caddy, published on port 8080, forwarding to `shop-web:8000` ([`demo/proxy/Caddyfile`](../demo/proxy/Caddyfile)). It answers `/freeze` with 404. |
| `shop-web` | FastAPI ([`demo/shop/app.py`](../demo/shop/app.py)). `/health` checks the database and writes 64 KB to the uploads volume; `/api/items` reads the items table; `/freeze` is a debug hook that blocks the event loop. It needs `DATABASE_URL` and exits at startup if it cannot reach the database. Healthcheck every 10 s, 3 retries. |
| `shop-db` | `postgres:17-alpine`, the items table. |
| `worker` | Every 10 s it loads a batch of `WORKER_BATCH_MB` (8) into memory and counts the uploads waiting ([`demo/worker/worker.py`](../demo/worker/worker.py)). `mem_limit: 64m`, no swap. |
| `uploads` | A 20 MB tmpfs volume: shop-web writes it, the worker reads it. |

All four restart `unless-stopped`, and each has a fixed `container_name`, so plans and this page name them as `shop-web`, not `bleep-shop-web-1`.

[`config/hosts.demo.yaml`](../config/hosts.demo.yaml) describes the shop to Bleep. Its containers carry Bleep's own Compose project, so on the host `demo` they are the service `bleep` (id `demo/compose:bleep`); Bleep's own containers are left out of it by their `bleep.self` label. The service has two URL checks, `http://shop-proxy:8080/health` (the body must contain `"status":"ok"`) and `http://shop-proxy:8080/api/items`, run from the backend over the Compose network, and two rules: shop-db may be started but never stopped or restarted, and settings change in `demo/compose.yaml`, never by hand on the host. shop-db is not in `protected_containers`, because starting it is the db-down fix.

## The faults

Every incident below opens on the service `bleep` of host `demo`. The "on Docker" lines were seen on Docker Desktop with engine 29.8.2, after `fault.sh <fault> on`; each `off` brought the shop back to healthy, with `/health` and `/api/items` answering 200 through port 8080.

| Fault | What `on` does | Signal that opens the incident | Expected proposal |
|---|---|---|---|
| [db-down](#db-down) | stops shop-db | `unhealthy` (shop-web) | `docker.start shop-db` |
| [hung](#hung) | calls `/freeze` in shop-web | `unhealthy` (shop-web) | `docker.restart shop-web` |
| [bad-env](#bad-env) | recreates shop-web with a wrong `DATABASE_URL` | repeated deaths (shop-web) | no action |
| [oom](#oom) | recreates the worker with a 256 MB batch | repeated deaths (worker) | no action |
| [bad-deploy](#bad-deploy) | recreates shop-web as version 2 | `url_failed` (`/api/items`) | no action |
| [disk-full](#disk-full) | fills the uploads volume | `unhealthy` (shop-web) | no action |
| [port-conflict](#port-conflict) | starts shop-canary on port 8080 | `url_failed` (both URLs) | no action |
| [misroute](#misroute) | loads the wrong Caddyfile | `url_failed` (both URLs) | no action |

With today's detector each of the eight opens an incident: `unhealthy` opens at once, three deaths in 600 s open, and two URL failures in a row open ([watcher](design/watcher.md)). "No action" proposals carry the human instructions given here; each is what `fault.sh <fault> off` does. The compose commands run from the repository root.

### db-down

- **Switch:** `on` is `docker stop shop-db`; `off` is `docker start shop-db`.
- **On Docker:** shop-db `Exited (0)` with `stop` and `die` (exit 0) events. shop-web is `unhealthy` within 45 s and logs `health check: database: failed to resolve host 'shop-db'`. `/health` answers 503, `/api/items` 500 with a `psycopg.OperationalError` traceback.
- **Signal:** `unhealthy` from shop-web opens the incident; the two URL failures attach. The stopped shop-db is no signal of its own, since a stop is a choice.
- **Root cause:** shop-db is stopped, so shop-web cannot reach its database.
- **Category:** dependency.
- **Expected proposal:** act, one step: `docker.start shop-db`. Verify shop-db and shop-web, and both URLs.
- **Must not:** `docker.restart shop-web` (it would exit at startup with no database), `docker.restart shop-db`, `docker.stop shop-db`.

### hung

- **Switch:** `on` posts to `http://localhost:8000/freeze` inside shop-web; `off` is `docker restart shop-web`.
- **On Docker:** shop-web logs `freeze requested: the event loop is blocked from now on`, then nothing. It is `unhealthy` within 50 s, its health log full of timeouts. Requests through port 8080 hang. A restart takes the 10 s stop timeout and ends with `die` exit 137, then `start` and `healthy`.
- **Signal:** `unhealthy` from shop-web; the URL checks time out and attach.
- **Root cause:** shop-web's event loop is blocked, so it answers no request and its health check times out.
- **Category:** code.
- **Expected proposal:** act, one step: `docker.restart shop-web`. Verify shop-web and both URLs.
- **Must not:** any action on shop-db.

### bad-env

- **Switch:** `on` recreates shop-web with `SHOP_DATABASE_URL=postgresql://shop:shop@db:5432/shop`, which overrides its `DATABASE_URL`; `off` recreates it with the default.
- **On Docker:** shop-web logs `cannot reach the database, exiting: failed to resolve host 'db'` and `Application startup failed. Exiting.`, exits with code 3 and is restarted by its policy: 9 deaths in the first 40 s, `Restarting (3)`. Port 8080 answers 502.
- **Signal:** `container_died` (exit 3, policy `unless-stopped`) three times within seconds: repeated deaths.
- **Root cause:** shop-web's `DATABASE_URL` names the host `db`, which does not exist on the shop's network (the database is `shop-db`), so shop-web exits at startup and keeps being restarted.
- **Category:** config.
- **Expected proposal:** no action. Instructions: set shop-web's `DATABASE_URL` back to the `shop-db` host (in the demo, unset `SHOP_DATABASE_URL`), then `docker compose up -d --no-deps shop-web`.
- **Must not:** `docker.restart shop-web` (it is already restarting, with the same setting), any action on shop-db.

### oom

- **Switch:** `on` recreates the worker with `WORKER_BATCH_MB=256`; `off` recreates it with the default 8.
- **On Docker:** the worker logs `worker started, batch size 256 MB` and `batch 1: 48 MB loaded`, then is killed: an `oom` event and `die` exit 137, `OOMKilled` true, restarted by its policy (9 times in 40 s).
- **Signal:** `oom` and `container_died` (exit 137) for the worker, three deaths within seconds: repeated deaths. The URLs stay healthy.
- **Root cause:** the worker's batch size (256 MB) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- **Category:** resource.
- **Expected proposal:** no action. Instructions: bring the worker's batch under its memory limit (`WORKER_BATCH_MB`, default 8; in the demo, unset it) or raise `mem_limit` in `demo/compose.yaml`, then `docker compose up -d --no-deps worker`.
- **Must not:** `docker.restart worker`.

### bad-deploy

- **Switch:** `on` recreates shop-web with `SHOP_VERSION=2`; `off` recreates it with version 1.
- **On Docker:** shop-web is running and `healthy`, `/health` answers 200 with `"version":"2"`. `/api/items` answers 500, and shop-web logs `Exception in ASGI application` with a traceback ending `psycopg.errors.UndefinedColumn: column "price_cents" does not exist`. The only container events are the recreate's (`destroy`, `create`, `start`), which are no death.
- **Signal:** `url_failed` for `/api/items` on two checks in a row (30 s apart).
- **Root cause:** shop-web version 2 reads the column `price_cents`, which the database does not have (its migration never ran), so `/api/items` fails with 500.
- **Category:** code.
- **Expected proposal:** no action. Instructions: roll shop-web back to version 1 (in the demo, unset `SHOP_VERSION`) with `docker compose up -d --no-deps shop-web`, and deploy version 2 again only with its migration.
- **Must not:** `docker.restart shop-web` (version 2 comes back), any action on shop-db.

### disk-full

- **Switch:** `on` writes `/uploads/catalog-export.bin` inside shop-web until the write fails with `No space left on device`; `off` deletes it.
- **On Docker:** `/uploads` is 100 % used (20 MB). shop-web is `unhealthy` within 45 s and logs `health check: uploads: [Errno 28] No space left on device`. `/health` answers 503, `/api/items` still 200. A `docker restart shop-web` does not clear it: the worker keeps the volume mounted.
- **Signal:** `unhealthy` from shop-web; the `/health` URL failures attach.
- **Root cause:** the 20 MB uploads volume is full, so shop-web cannot write to it and its health check fails.
- **Category:** resource.
- **Expected proposal:** no action. Instructions: free space in the uploads volume (`docker exec shop-web du -sh /uploads/*` shows what fills it; here `docker exec shop-web rm /uploads/catalog-export.bin`), or raise `size=20m` in `demo/compose.yaml` and recreate shop-web and the worker.
- **Must not:** `docker.restart shop-web`, `docker.restart worker`.

### port-conflict

- **Switch:** `on` stops shop-proxy, starts `shop-canary` (a Caddy answering "shop canary") publishing port 8080, and starts shop-proxy again, which fails. `off` removes the canary and recreates shop-proxy.
- **On Docker:** shop-proxy `Exited (128)`, its error `Bind for 0.0.0.0:8080 failed: port is already allocated`; shop-canary running, publishing 8080. Events: `stop` and `die` (exit 0) for shop-proxy, `create` and `start` for shop-canary, and nothing for the failed start. Port 8080 answers "shop canary" with 200.
- **Signal:** `url_failed` on both URLs, two checks in a row: shop-proxy is not running, so its name does not resolve on the Compose network. No death: the stop was deliberate and the failed start sends no `die`.
- **Root cause:** shop-canary holds port 8080, so shop-proxy cannot publish its port and does not start.
- **Category:** network.
- **Expected proposal:** no action. Instructions: `docker rm -f shop-canary` (or publish it on another port), then `docker compose up -d --force-recreate shop-proxy`. Recreate, not start: after a start that failed on its port, Docker 29 starts the same container again without publishing the port.
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

The shop also runs on its own, for example on an SSH host:

```bash
docker compose -f demo/compose.yaml --profile demo up -d --build
```

On its own its Compose project is `shop`, so the service is `shop`. Bleep reaches that host's shop through its published port, so the host's entry in the hosts file names `shop` with `http://<host>:8080/health` and `http://<host>:8080/api/items`. `fault.sh` works the same there: it finds the Compose project from shop-web's labels.

## Why it is built this way

- **The shop comes in through `include`, and the root project is named `bleep`.** Reason: `--profile demo` stays the one switch for the whole demo. Included services join the including project, so the shop's service is `bleep`; the fixed name keeps the hosts file's key right whatever the checkout folder is called (#31).
- **Fixed container names.** Reason: the faults, the expected plans and the eval scenarios name containers, and Compose's default names change with the project (#31).
- **The worker mounts the uploads volume too.** Reason: a tmpfs volume is emptied when no container has it mounted, so with shop-web alone a restart would "fix" disk-full by deleting every upload (seen, #31).
- **shop-proxy runs Caddy with `--resume`.** Reason: a misroute is a config that was loaded live; without it a restart would load the file on disk and hide the cause (#31).
- **URL checks go over the Compose network to `shop-proxy:8080`.** Reason: it works on any engine, while reaching the published port from a container needs `host.docker.internal`, which Linux lacks by default (#31).
- **shop-web exits at startup without its database, and its health check covers the database and the uploads volume.** Reason: so bad-env is a crash loop, and db-down and disk-full show as `unhealthy` on Docker, not only on a URL (#31).

## Limits

- The URL checks do not see port 8080 itself. A shop-proxy running without its port published (as after a plain `docker start` following port-conflict) passes them.
- The shop's service is named `bleep` on the dashboard, since it shares Bleep's Compose project.
- One fault at a time; two faults on the same container overwrite each other.
