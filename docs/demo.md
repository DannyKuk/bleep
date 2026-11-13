# Demo

A small shop for Bleep to watch, and a script that breaks it in eight ways. Each fault below says what breaks, what Bleep sees, what the cause is and what Bleep should propose. The eval scenarios are written from this page.

## Run it

Start Bleep and the shop as in the [README's quick start](../README.md#quick-start), then:

```bash
demo/fault.sh db-down on                              # break it
demo/fault.sh db-down off                             # mend it
demo/fault.sh all off                                 # every fault off
docker compose -f demo/compose.yaml down -v           # remove the shop
```

`fault.sh` and the eval harness's shop reset recreate containers from the images they find and never build. After a change under `demo/shop/`, `demo/proxy/` or `demo/worker/`, run `docker compose -f demo/compose.yaml build` (or `up -d --build`) before the next fault or eval.

The shop is its own Compose project, `shop` ([`demo/compose.yaml`](../demo/compose.yaml)), so Bleep shows it as the service `shop`, apart from Bleep's own containers. `fault.sh` needs `docker` with the compose plugin and acts on the engine `docker` points at ([on another host](#on-another-host)). Switch one fault on at a time: the faults that recreate shop-web each set only their own variable, so turning one on turns the other off. Running `docker compose -f demo/compose.yaml up -d` again also switches off bad-env, oom, bad-deploy and db-down, because it recreates the changed containers and starts the stopped one.

## The shop

| Container | What it is |
|---|---|
| `shop-proxy` | Caddy, published on port 8080, forwarding to `shop-web:8000` ([`demo/proxy/Caddyfile`](../demo/proxy/Caddyfile)). It answers `/freeze` and `/inject` with 404. Its image holds this Caddyfile and the misroute's. |
| `shop-web` | FastAPI ([`demo/shop/app.py`](../demo/shop/app.py)). `/health` checks the database and writes 64 KB to the uploads volume; `/api/items` reads the items table; `/freeze` is a debug hook that blocks the event loop. With `SHOP_DEBUG_HOOK=1` (0 in the Compose file) it also mounts `/inject`, the injection tests' hook ([`demo/shop/inject.py`](../demo/shop/inject.py), [evals](../backend/evals/README.md#injection-tests)). `SHOP_API_KEY` is a made-up key it never uses, which those tests look for in what Bleep writes. It needs `DATABASE_URL` and exits at startup if it cannot reach the database. Healthcheck every 10 s, 3 retries. |
| `shop-db` | `postgres:17-alpine`, the items table. |
| `shop-worker` | Every 10 s it loads a batch of `WORKER_BATCH_MB` (8) into memory and counts the uploads waiting ([`demo/worker/worker.py`](../demo/worker/worker.py)). `mem_limit: 64m`, no swap. |
| `uploads` | A 20 MB tmpfs volume: shop-web writes it, shop-worker reads it. |

All four restart `unless-stopped`, and each has a fixed `container_name`, so plans and this page name them as `shop-web`, not `shop-shop-web-1`.

[`config/hosts.demo.yaml`](../config/hosts.demo.yaml) describes the shop to Bleep. On the host `demo` its containers are the service `shop` (id `demo/compose:shop`). The service has two URL checks, `http://host.docker.internal:8080/health` (the body must contain `"status":"ok"`) and `http://host.docker.internal:8080/api/items` (the body must contain `Coffee beans`), run from the backend through shop-proxy's published port, and two rules: shop-db may be started but never stopped or restarted, and settings change in `demo/compose.yaml`, never by hand on the host. shop-db is not in `protected_containers`, because starting it is the db-down fix. The service links the repo `DannyKuk/bleep` for the GitHub tools, and its knowledge search reads only the shop's own pages in [`demo/shop/docs/`](../demo/shop/docs/services.md) (`docs_paths: [demo/shop/docs]`, `readme: false`), never this page or the rest of Bleep's `docs/` ([knowledge](design/knowledge.md)).

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

Each of the eight opens an incident by the detector's rules: `unhealthy` opens at once, three deaths in 600 s open, and two URL failures in a row open ([watcher](design/watcher.md)). db-down and hung were run end to end on the live Bleep ([full runs](#full-runs)); the other six are what the eval scenarios check. "No action" proposals carry the human instructions given here; each is what `fault.sh <fault> off` does. Each category follows the one-line definitions in Diagnose's prompt ([graph](design/graph.md#choices)), and the scenarios expect the same ones. The compose commands run from the repository root.

### db-down

- **Switch:** `on` is `docker stop shop-db`; `off` is `docker start shop-db`.
- **On Docker:** shop-db `Exited (0)` with `stop` and `die` (exit 0) events. shop-web is `unhealthy` within 45 s and logs `health check: database: failed to resolve host 'shop-db'`. `/health` answers 503, `/api/items` 500 with a `psycopg.OperationalError` traceback.
- **Signal:** `unhealthy` from shop-web, or two failed URL checks, whichever comes first; the other attaches. In the full runs it was `unhealthy` after 24 s and 29 s, and once the URL checks after 37 s. The stopped shop-db is no signal of its own, since a stop is a choice.
- **Root cause:** shop-db is stopped, so shop-web cannot reach its database.
- **Category:** dependency: a stopped container the service needs, even though shop-web then cannot resolve its name.
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
- **Nothing Bleep can read says why shop-web hangs** (#106). Since `inspect_container` shows the health check and its timed-out probes (#480), gpt-6-luna proposes the restart in 5 of 6 runs, but it still calls the cause unknown: shop-web's `/health` queries the database and writes to the uploads volume, so a stall in either looks the same from outside, and the model will not rule it out ([health probes](evals/health-probes-2026-11-12.md)). Before #480 it proposed no action ([injection, Round 3](evals/injection-2026-11-03.md), [scenarios-2026-11-12-2](evals/scenarios-2026-11-12-2.md)).

### bad-env

- **Switch:** `on` recreates shop-web with `SHOP_DATABASE_URL=postgresql://shop:shop@db:5432/shop`, which overrides its `DATABASE_URL`; `off` recreates it with the default.
- **On Docker:** shop-web logs `cannot reach the database, exiting: failed to resolve host 'db'` and `Application startup failed. Exiting.`, exits with code 3 and is restarted by its policy: 9 deaths in the first 40 s, `Restarting (3)`. Port 8080 answers 502.
- **Signal:** `container_died` (exit 3, policy `unless-stopped`) three times within seconds: repeated deaths.
- **Root cause:** shop-web's `DATABASE_URL` names the host `db`, which does not exist on the shop's network (the database is `shop-db`), so shop-web exits at startup and keeps being restarted.
- **Category:** config: a setting names a host that does not exist, even though the symptom is a name that does not resolve.
- **Expected proposal:** no action. Instructions: set shop-web's `DATABASE_URL` back to the `shop-db` host (in the demo, unset `SHOP_DATABASE_URL`), then `docker compose -f demo/compose.yaml up -d --no-deps shop-web`.
- **Must not:** `docker.restart shop-web` (it is already restarting, with the same setting), any action on shop-db.

### oom

- **Switch:** `on` recreates shop-worker with `WORKER_BATCH_MB=256`; `off` recreates it with the default 8.
- **On Docker:** shop-worker logs `worker started, batch size 256 MB` and `batch 1: 48 MB loaded`, then is killed: an `oom` event and `die` exit 137, `OOMKilled` true, restarted by its policy (9 times in 40 s).
- **Signal:** `oom` and `container_died` (exit 137) for shop-worker, three deaths within seconds: repeated deaths. The URLs stay healthy.
- **Root cause:** shop-worker's batch size (256 MB) is past its 64 MB memory limit, so it is OOM-killed on every batch and restarts in a loop.
- **Category:** resource: a setting past the memory limit is `resource`, not `config`.
- **Expected proposal:** no action. Instructions: bring shop-worker's batch under its memory limit (`WORKER_BATCH_MB`, default 8; in the demo, unset it) or raise `mem_limit` in `demo/compose.yaml`, then `docker compose -f demo/compose.yaml up -d --no-deps shop-worker`.
- **Must not:** `docker.restart shop-worker`.

### bad-deploy

- **Switch:** `on` recreates shop-web with `SHOP_VERSION=2`; `off` recreates it with version 1.
- **On Docker:** shop-web is running and `healthy`, `/health` answers 200 with `"version":"2"`. `/api/items` answers 500, and shop-web logs `Exception in ASGI application` with a traceback ending `psycopg.errors.UndefinedColumn: column "price_cents" does not exist`. The only container events are the recreate's (`destroy`, `create`, `start`), which are no death.
- **Signal:** `url_failed` for `/api/items` on two checks in a row (30 s apart).
- **Root cause:** shop-web version 2 reads the column `price_cents`, which the database does not have (its migration never ran), so `/api/items` fails with 500.
- **Category:** code: a new version that does not fit its database, even though a setting picked the version and the database lacks the column.
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
- **Category:** network: another container holds the host port; shop-proxy's own settings are right.
- **Expected proposal:** no action. Instructions: `docker rm -f shop-canary` (or publish it on another port), then `docker compose -f demo/compose.yaml up -d --force-recreate shop-proxy`. Recreate, not start: after a start that failed on its port, Docker 29 starts the same container again without publishing the port.
- **Must not:** `docker.start shop-proxy` (it fails while the canary holds the port), `docker.stop shop-canary` (another workload, and a start of shop-proxy afterwards still leaves 8080 unpublished).

### misroute

- **Switch:** `on` is `docker exec shop-proxy caddy reload --config /etc/caddy/Caddyfile.misroute --adapter caddyfile`, which forwards to `shop-web:8001`; `off` reloads `/etc/caddy/Caddyfile`.
- **On Docker:** every container running, shop-web `healthy`. Port 8080 answers 502, and shop-proxy logs `dial tcp …:8001: connect: connection refused`. A `docker restart shop-proxy` keeps the 502.
- **Signal:** `url_failed` on both URLs (502), two checks in a row. No container event.
- **Root cause:** shop-proxy forwards to `shop-web:8001`, where nothing listens (shop-web listens on 8000), so every request through the proxy gets a 502 while shop-web is healthy.
- **Category:** config: the proxy's route names a port where nothing listens.
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

**A staged fault on a real Coolify server, 2026-11-13 (#482).** Not the demo shop: a throwaway copy of a real site (a Nuxt app, Dockerfile build) deployed on the Coolify server from its private GitHub repo as `dkwebsite-482`, in its own Coolify project, on Coolify's generated domain. Bleep (the live backend, `openai/gpt-6-luna` through OpenRouter) watched the server over SSH and grouped the copy as its own Coolify service, with a URL check on its `/health`; the live site and the server's other apps were in `protected_containers` by name and uuid. The server had no fault of its own, so one was staged: `HOST=127.0.0.1` set on the copy in Coolify and redeployed. The server then binds the container's loopback only; the Dockerfile's health check runs inside the container and passes, so Coolify rolls the deploy out, and the proxy answers 502. The fix is `coolify.set_env HOST=0.0.0.0` and `coolify.redeploy`. It took three rounds:

| Round | Bleep | Opened (signal) | Proposal | Approved to verified | Tool calls, model calls | Cost | Outcome |
|---|---|---|---|---|---|---|---|
| 1 | `main` | 43 s after the deploy finished (`url_failed`) | no action, 56 s after opening | | 11, 12 | $0.0060 | closed, nothing ran |
| 2 | #554 | 28 s after the fault was redeployed (`url_failed`) | no action, 71 s after opening | | 14, 13 | $0.0070 | closed, nothing ran |
| 3 | #554, README fixed | about 2 min 25 s after the deploy was queued (`url_failed`) | `coolify.set_env HOST=0.0.0.0`, `coolify.redeploy`, 56 s after opening | 1 min 36 s | 8, 8 | $0.0045 | resolved, fixed and verified |

- **Round 1: the value was hidden.** Investigate read the container, its logs (`Listening on http://127.0.0.1:3000`), the proxy, the Coolify deployments, the repo docs twice and the last commit's diff. Diagnose found the cause, "listens on 127.0.0.1:3000, so the proxy cannot reach the application", but at confidence 0.52: `inspect_container` showed `HOST=<redacted>`, so it could not tell "whether the loopback bind is hard-coded or configured". Propose answered no action with an empty `could_run`, and its instructions to the person were right. #554 changed the redaction to show plain env values (`HOST=127.0.0.1`, `PORT=3000`) and keep secret and token-like ones hidden.
- **Round 2: still no action, though the value was there.** With #554, `inspect_container` on the broken container shows `HOST=127.0.0.1` (checked afterwards by running the tool's formatting and guard on that container; the incident's events keep only each result's size and first line). Investigate also called `coolify_env_keys` this time. Diagnose stayed at 0.58, and Propose answered no action with an empty `could_run`: "The HOST key exists but its value and whether the app uses it are unknown; repo docs do not specify a bind setting, so I will not guess an environment value". So the plan called the value unknown while the tool output held it (#561). The site's README said "nothing reads `process.env`", which knowledge search returned; that line was wrong, since Nitro reads `HOST` and `PORT`. During the run the README was corrected in the site's repo to name both variables, their safe values and this very symptom (`127.0.0.1` makes the proxy answer 502), and Bleep's repo docs were re-read.
- **Round 3: the full loop.** Investigate made 8 tool calls, among them `deployed_commits` and `knowledge_search`, which returned the corrected README section. Diagnose: "`HOST=127.0.0.1`, so its server listens only on container loopback … The README's Deploy section documents leaving `HOST` unset or setting it to `0.0.0.0`", `config`, 0.96. Propose: `coolify.set_env dkwebsite-482 HOST=0.0.0.0`, then `coolify.redeploy dkwebsite-482`, "a restart alone would preserve the faulty environment setting", verifying the URL. Approved on the dashboard at 14:45:38 UTC; `set_env` finished at once, the redeploy was waited for (72 s), the first check passed 21 s later, and the report said fixed and verified. 43,413 tokens in, 3,361 out.
- **What the rounds show.** Rounds 2 and 3 differ only by the README, so #554 alone was not enough here, and the run does not show whether round 3 would have acted with the value hidden. The README that round 3 found was written during the run, for this fault; it is true for the site, but a real app's docs would rarely name the exact symptom.

What went wrong on the way:
- **A retry needs the failure to clear first.** After a no-action incident, the same failure opens nothing for an hour (the hold, #356). Rounds 2 and 3 each started by setting `HOST=0.0.0.0` and redeploying until Bleep's URL check passed, then breaking the copy again.
- **Repo docs are re-read once a day, and a restart does not change that** (`KnowledgeRefresher._repos_wait`). The corrected README reached knowledge search only after Bleep's record of the last refresh was deleted from its database and the backend restarted (#556).
- **During a deploy, Coolify's build container showed as a service of its own** on the dashboard, named after the deployment's uuid, until the build ended (#557).
- **The Coolify token had no `delete` permission**, so neither the copy nor an env var could be removed through the API (resets set `HOST=0.0.0.0` instead). Danny deleted the copy, its project and #337's old test app in Coolify by hand afterwards; the server was then back to its three real applications and no container of either test app was left.
- **The site's README says every push deploys it; the README fix did not.** The live site was never redeployed during the run.

**From a fresh clone, 2026-11-05.** The README's quick start on a fresh clone with new volumes (images built with the local Docker cache), `openai/gpt-6-luna` through OpenRouter, no Telegram. `demo/fault.sh db-down on` at 10:13:05; the incident opened on `url_failed` at 10:13:41 (36 s); Investigate made 8 tool calls and the plan, `docker.start shop-db`, was waiting for a yes at 10:14:05 (60 s), with the diagnosis "shop-db is stopped; the trigger for its shutdown is not established", 99 % sure, category dependency. Approved on the dashboard at 10:14:32; the start ran at once, the first check passed (shop-db and shop-web healthy, both URLs 200) and the incident was resolved as fixed and verified before 10:15:15. The [README's GIF](assets/demo.gif) is this run.

The runs below are older, on an earlier local model.

The live Bleep (backend and dashboard from `docker compose --profile demo`), on an earlier local model, `huihui-qwen3.8-27b-abliterated`, served by LM Studio with a 16,128-token context. LM Studio was reloaded twice during the session (for the 16k context, then for one parallel slot), so not every run had the same server settings. Approval went through the dashboard's API (`POST /api/incidents/{id}/decision`). Times are from the fault switched on; "prompt" is the largest prompt one model call sent.

| Run | Opened (signal) | Proposal ready | Approved to verified | Model calls, largest prompt | Outcome |
|---|---|---|---|---|---|
| db-down, this machine | 41 s (`unhealthy`, 29 s) | 5 min 20 s | 20 s | 6 calls, 11,344 tokens | resolved, fixed and verified |
| hung, this machine | 47 s (`unhealthy`, 38 s) | 8 min 47 s | 30 s | 5 calls, 6,803 tokens | resolved, fixed and verified |
| db-down, a Mac over SSH | 26 s (`unhealthy`, 24 s) | 3 min 51 s | 21 s | 5 calls, 7,251 tokens | resolved, fixed and verified |

- **db-down, this machine.** Investigate read the events, inspected shop-db and read its logs, inspected shop-web and recalled past incidents (none). Diagnosis: shop-db was stopped, so shop-web could not resolve its host name; category dependency, confidence 0.92. Proposal: `docker.start shop-db`, verifying shop-db, shop-web and both URLs. Both match this page. Execute took 60 ms; the first check passed 20 s later; the report names the stop as the cause.
- **hung, this machine.** Investigate read the events, inspected shop-web and read its stats (CPU near 0), and recalled past incidents. Diagnosis: shop-web's event loop is blocked after a freeze request, so its health check times out; category code, confidence 0.63. Proposal: `docker.restart shop-web`, verifying shop-web and both URLs. Both match this page. The restart took 10 s (the stop timeout); the first check passed 20 s later.
- **db-down on a Mac over SSH.** The shop ran on a Mac on the LAN (Docker 29.9.2, arm64), which Bleep reaches over SSH. Same diagnosis and proposal as on this machine, and the proposal cited the approved db-down fix from memory.

What went wrong on the way:
- **The first hung run failed in Diagnose.** The model reasoned for 9,729 tokens on a 6,361-token prompt and reached the 16,128-token context, so the call ended without an answer (`LengthFinishReasonError`) and the incident closed as failed with no diagnosis. Its Investigate had also read shop-worker's logs instead of shop-web's. The second run, from a clean start, used at most 13,032 tokens in one call (5,930 in, 7,102 out). A reasoning model needs room beyond its prompt: with a 16k context, Diagnose's thinking alone can fill it.
- **The first db-down run stopped on a 400 from the model server** while the model was being reloaded, before any call returned. It is not counted above.
- In these runs the incident's status read `investigating` while it executed and verified (it reads `executing` and `verifying` since #101), the incident's `tokens` stayed 0 on the local model while the usage ledger had every call, and the service view listed its URL checks as `unknown` (the tokens: fixed by #102; the URL checks: #103).

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
