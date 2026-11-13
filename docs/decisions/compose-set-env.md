# Compose set_env: changing one env key of a plain Compose service

> **Answer:** **no, for now** (decided 2026-11-09). Bleep keeps "no action" with exact instructions for a config fault on a plain Compose host, and does not edit files on a host. The evidence: on a socket host (the laptop, through the [socket proxy](socket-host.md)) Bleep has no file access and the proxy refuses the container create and delete a Compose recreate needs, so `compose.set_env` could fix nothing there without undoing that decision. On an `ssh` host a file edit can fix a value only when it lives in a file the container's labels lead to (5 of the 11 places a value can come from). Of the 14 Compose containers whose labels were read for this spike, 1 named a file that exists on its host and is not managed by Coolify, and the value both demo config faults (bad-env, oom) break is set in the shell that ran `docker compose up`, which no file holds. The value bad-env needs back is a database URL with a password in it, and the plan check already refuses such a value as a hidden param (#294), so even a working `compose.set_env` could not carry that fix to a yes. The options and what each costs are under [The options](#the-options); the narrowest design, if the answer is yes, is under [If the answer were yes](#if-the-answer-were-yes-the-narrowest-design).

## Question

Should Bleep fix a wrong setting on a plain Docker Compose host after an explicit yes, and if so, how narrowly? Today a config fault there always ends in "no action" with instructions for the human: the act tools are `docker.start`, `docker.restart` and `docker.stop`, plus `coolify.set_env` and `coolify.redeploy` on Coolify hosts ([plugins](../design/plugins.md#coolify)). On plain Compose, changing an env value means editing a file on the host and recreating the container, a file write that the design rules out: no generic shell with write access, ever ([guard](../design/guard.md#act-tools), [hosts](../design/hosts.md)).

The candidate looked at is `compose.set_env`: change one key in one file that the container's own Compose labels name (`com.docker.compose.project.config_files`, `com.docker.compose.project.working_dir`, or the project's `.env`), then recreate only that service (`docker compose up -d --no-deps <service>`), keeping the old value for a rollback.

## What we tried

All on 2026-11-09, on the laptop (Docker Desktop, Engine 29.8.2, Compose 5.6.0). Nothing was started, stopped or recreated; every command below only reads.

- **Bleep's code** as it is on `main`: the shell allowlist (`hosts/shell.py`), the SSH session (`hosts/ssh.py`), the act tools (`tools/docker_act.py`), the Coolify actions (`plugins/coolify/plugin.py`, `client.py`), the plan check (`guard/plan_check.py`) and the demo (`demo/compose.yaml`, `demo/fault.sh`).
- **Compose's documentation and source:** [environment variable precedence](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/), [interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/), [`docker compose up`](https://docs.docker.com/reference/cli/docker/compose/up/), the label names in [`pkg/api/labels.go`](https://github.com/docker/compose/blob/main/pkg/api/labels.go) and where they are set in [`internal/projectloader/loader.go`](https://github.com/docker/compose/blob/main/internal/projectloader/loader.go).
- **The Engine API** [v1.47 spec](https://docs.docker.com/reference/api/engine/version/v1.47.yaml): what `POST /containers/{id}/update` accepts.
- **Precedence, measured:** a throwaway Compose file in a temp directory (one service, an `env_file:`, an `environment:` entry, a `${DB_HOST:-default-host}` interpolation, a `.env` and a second env file), rendered with `docker compose config`, which creates nothing.
- **The demo's own files:** `docker compose -f demo/compose.yaml config shop-web` with and without the bad-env variable, `--no-interpolate`, and `config --hash`.
- **Labels on real containers:** `docker inspect` of the four running shop containers and two of Bleep's own, and the label sets saved by the [SSH spike](../spikes/ssh-docker/report.md#labels) on 2026-11-02 (one Mac container, nine Compose containers on a Coolify server).
- **The plan check on candidate values:** `guard/redact.hides_params` on a `coolify.set_env`-shaped step with the values a fix would need.

## Result

**Where a container's env value comes from, and whether editing a file the labels lead to can change it.** Precedence as documented and as measured with `config`: shell beats `--env-file` beats `.env` for interpolation, and `environment:` beats `env_file:`.

| Where the value lives | Measured with `config` | A file edit fixes it? |
|---|---|---|
| A literal in `environment:` of a labelled Compose file | `BOTH: from-environment` over `env_file:` | yes, by editing YAML (comments, anchors, list or map form, override files) |
| `${VAR}` filled from the project's `.env` | `from-dotenv` | yes, one line, if no shell sets `VAR` at the next `up` |
| `${VAR}` filled from `--env-file` | `from-env-file-flag`; the label `com.docker.compose.project.environment_file` names the file | yes, if the recreate passes the same file |
| `env_file:` of the service | `ONLY_ENV_FILE: from-env_file`; no label names this file, only `config` does | yes |
| `${VAR:-default}` with nothing set | `default-host` | yes, by adding `VAR` to `.env` or editing the default |
| `${VAR}` from the shell that ran `up` | `from-shell` beats `.env`; no label records it | **no**: no file holds it |
| The image's `ENV` | | no: a rebuild |
| A container made by `docker run -e` (no Compose labels) | | no |
| A Compose file read from stdin | label `config_files` is `-` | no |
| A labelled file that no longer exists (moved or deleted checkout) | | no |
| A Coolify application | labels point at `/artifacts/<id>/…`, regenerated by each deploy | no; `coolify.set_env` covers it |

5 of the 11 can be fixed by a file edit, and only on a host where Bleep can reach that file.

**What the labels pointed at on 14 Compose containers:**

| Containers | `config_files` | `environment_file` | A plain user file that exists? |
|---|---|---|---|
| shop-web (laptop) | `<repo>/demo/compose.yaml` | none | yes |
| shop-db, shop-worker, shop-proxy (laptop) | `<repo>/.claude/worktrees/<deleted>/demo/compose.yaml` | none | no: the checkout they were last created from was deleted |
| 4 Coolify applications (Coolify server) | `/artifacts/<id>/docker-compose*.yaml` | `/artifacts/<id>/.env` on 2 | no: Coolify's deploy files |
| `coolify`, `coolify-db`, `coolify-redis` (Coolify server) | `/data/coolify/source/docker-compose.yml,…prod.yml` | `/data/coolify/source/.env` | Coolify's own install, not checked on the host |
| `coolify-proxy` (Coolify server) | `/data/coolify/proxy/docker-compose.yml` | none | Coolify-managed (`coolify.managed=true`) |
| a probe container (Mac, SSH spike) | `-` (stdin) | none | no |

`environment_file` is set only when `--env-file` was passed (`loader.go`: `if len(options.EnvFiles) != 0`); the default `.env` and the shell are never recorded. Bleep's own two containers name `<repo>/docker-compose.yaml` (one with the observability override as a second file); they carry `bleep.self=true` and are never targets.

**The demo's two config faults.** `demo/compose.yaml` writes `DATABASE_URL: ${SHOP_DATABASE_URL:-postgresql://shop:shop@shop-db:5432/shop}` and `WORKER_BATCH_MB: ${WORKER_BATCH_MB:-8}`; `demo/fault.sh` turns bad-env and oom on by setting `SHOP_DATABASE_URL` or `WORKER_BATCH_MB` in the shell for one `compose up -d --no-deps`. There is no `demo/.env`. So both faults are the "shell" row: no file holds the bad value.

| shop-web | `config --hash shop-web` |
|---|---|
| The running container's `com.docker.compose.config-hash` label (fault off) | `5bd15687…` |
| The files as they stand, no shell variable | `5bd15687…` |
| The files with `SHOP_DATABASE_URL=…@db:5432/shop` in the shell | `3e543a9d…` |

**The value a fix needs, through the plan check's hidden-param rule** (`hides_params`, #294):

| Key | Value | Refused as `hidden_param` |
|---|---|---|
| `DATABASE_URL` | `postgresql://shop:shop@shop-db:5432/shop` | yes (a password in the URL) |
| `SHOP_DATABASE_URL` | the same | yes |
| `DATABASE_URL` | `postgresql://shop-db:5432/shop` | no, but it drops the user and password |
| `WORKER_BATCH_MB` | `8` | no |

**Can the Engine API change a container's env?** No. `POST /containers/{id}/update` takes the `Resources` fields (CPU, memory, block IO, devices, pids, ulimits) and `RestartPolicy`; `Env` is not among them. A new env means a new container: create, stop, rename, remove, start. [`docker compose up`](https://docs.docker.com/reference/cli/docker/compose/up/) does exactly that: when a service's configuration changed it "picks up the changes by stopping and recreating the containers (preserving mounted volumes)", and `--no-deps` means "Don't start linked services". The socket proxy refuses `POST /containers/create` and `DELETE /containers/<id>` with 403 ([socket host](socket-host.md#result)).

**What each host kind allows today:**

| | `ssh` host | `socket` host |
|---|---|---|
| Read a file on the host | only the fixed commands of the allowlist; the one file any of them reads is `/proc/loadavg` | no |
| Write a file | no command does | no |
| Run the Compose CLI | not allowlisted; `docker` may not be on the non-interactive `PATH` (it was not on the Mac, [Docker over SSH](docker-over-ssh.md#result)) | no CLI in the backend, and the proxy refuses create and delete |
| How a command is sent | an argv list joined with `shlex.join`, parsed by the remote user's login shell | — |

## What it shows

- **The demo cannot show `compose.set_env`.** The laptop is a socket host, and both config faults live in the shell. The fix the docs give for bad-env and oom is a recreate from the files as they stand, which needs no file edit at all.
- **On a socket host nothing in this direction works without undoing the socket-host decision.** A recreate needs the Compose CLI with the project's files and an engine that lets it create and delete containers; that is the root-equivalent access the proxy exists to keep out of the backend.
- **The labels are host text, and often stale.** 3 of the 4 shop containers point at a checkout that no longer exists, and every Coolify-made container points at Coolify's own deploy files. A label can lead Bleep to a file; it cannot show that the file is the one in use, or that a human still deploys from it. Only `config --hash` against the container's `config-hash` label shows that the files as they stand still produce the running container.
- **The model cannot write the fix for the commonest config fault.** It never sees a password in an env value (`redact_env`; plain values are shown since #554), so it does not know the password in a URL it would have to write whole, and a value with a password is refused before a human sees it. A design that could fix bad-env would have to edit only a part of the value (the host in a URL) in code, which is a larger feature than one key.
- **Recreating is the dangerous half, not the edit.** A recreate interpolates the whole service from Bleep's own SSH environment. Any value the human's shell or CI supplied at the last `up` is lost, and every setting that differs from the files comes back with it, not only the one key.

### The options

1. **No (chosen).** Keep "no action" for config faults on plain Compose, with instructions that name the key, where it is set and the command to run (#357 and #364 already make Diagnose name the key and the documented value). Cost: nothing to build; a config fault on plain Compose stays a human fix.
2. **A recreate only (`compose.recreate`), `ssh` hosts only.** No file is written: Bleep recreates one service from its labelled files as they stand, only when the container's `config-hash` label differs from `config --hash` of those files, and the approval card lists the env keys that would change (names, not values). It would fix bad-env and oom as the demo sets them, but not on the laptop. Cost: the first non-read command on a host (the Compose CLI, with a per-host `docker` path), a drift check, an approval card for it, tests and evals; a human who set a value in the shell on purpose loses it.
3. **`compose.set_env`, narrow, `ssh` hosts only** (the design below). Fixes a value held in a `.env`-style file the hosts file allows, when the value has no secret in it (a batch size, a host name, a flag). Cost: the first file write on a host, a fixed write script, YAML reading to map a container key to its file variable, rollback, file permissions for Bleep's SSH user, tests and evals; by the repo's 400-line rule more than one PR (an estimate). It fixes neither demo fault.
4. **Both 2 and 3.** Covers the shell case and the file case on `ssh` hosts, at both costs.

### If the answer were yes: the narrowest design

- **Opt-in per service in the hosts file.** A service override names the one env file Bleep may change (for example `env_file_writes: /srv/shop/.env`), and the action exists only for that host and service. The labels must agree with it (the file is the project's `.env` in `working_dir` or the `environment_file` label); a label alone never names a file Bleep may write.
- **Refused targets, in code:** a socket host; a container with `coolify.*` labels or a Compose file under `/artifacts/` or `/data/coolify/`; a protected container; Bleep's own; a container with no Compose labels or `config_files: -`.
- **Params exactly `key` and `value`.** The key matches `ENV_KEY` and appears in the file exactly once (Compose takes the last of a repeated key); the service's environment entry is exactly `${KEY}`, `${KEY:-…}` or `${KEY-…}` (read with `config --no-interpolate`), so the container key it feeds is known; no other service uses `KEY` (else the next human `up` changes that service too). The value is one line with no `'`, no control character, at most 10,000 characters, and passes `check_shown`, so it is shown in full on the approval card with the file path, the key and the container key it feeds.
- **A fixed command per step, no model text in any argv.** (1) `docker compose -p <project> --project-directory <working_dir> -f <file>… [--env-file <file>] config --hash <service>` must equal the container's `config-hash` label, else the files no longer produce the running container and the step is refused. (2) A fixed write script, run with the file path as its only argument and the new file on stdin: it checks the file's sha256 still equals the one recorded at approval, keeps `<file>.bleep-<incident>`, writes a temp file with the same mode in the same directory and renames it over. Bleep's code builds the new file: the one line `KEY='value'`, single-quoted so Compose does not interpolate it. The value never appears in an argv, a log line, the guard log or the database. (3) `docker compose … up -d --no-deps --no-build --pull never <service>`.
- **Plan check:** like `set_env_without_redeploy`, a set_env without its recreate is refused; the simplest form makes the edit and the recreate one action.
- **Rollback.** If the recreate exits non-zero, Bleep restores the backup and recreates again, and the approval card says so before the yes. If Verify fails (the new value is also wrong), the loop-back runs as today; restoring the backup is a step of its own (`compose.restore_env`) that needs its own yes, and a second set_env in the same incident keeps the first backup.
- **Verify** as today: the container runs healthy and its URLs answer.
- **Risks that stay:** the file may be tracked by git (the working tree is dirty, a later `git pull` conflicts or `git checkout` reverts it) or ignored (the fix lives on that host only); a CI job that runs `compose up` with its own `--env-file` or shell variables overwrites or overrides the change; a human edits the file at the same time (the sha256 check refuses, the human gets instructions); Bleep's SSH user usually cannot write the deploy user's files, so the opt-in also means changing permissions on the host; the remote login shell must be POSIX for `shlex.join` quoting to hold.

## Decided along the way

- **The Engine API cannot change a container's env.** Any env fix is a recreate, and a recreate of a Compose service belongs to the Compose CLI, so that its labels and `config-hash` stay true for the next human `up`. A container rebuilt by Bleep through the Engine API with a changed env would keep a hash that matches the files and hide the change until the next forced recreate.
- **Socket hosts are out of every option.** The proxy stays as [decided](socket-host.md); none of the options gives the backend create, delete or file access on the laptop.
- **Coolify-managed containers stay with `coolify.set_env`.** Their Compose files are Coolify's and are rewritten by the next deploy.
- **Until a feature issue lands, [demo](../demo.md#bad-env) keeps "no action" for bad-env and oom.**

## Limits

- Measured on one Docker Desktop host (Compose 5.6.0); precedence was rendered with `config`, not by running containers. The Coolify server's labels come from the SSH spike's saved captures (2026-11-02); whether the files they name exist was not checked, since this spike used no SSH.
- The 14 labelled containers are this project's demo and one Coolify server, not a survey of how people deploy Compose. A team that keeps every setting in a `.env` next to its Compose file would see more file-fixable faults.
- That fault.sh's bad-env recreate labels shop-web with `3e543a9d…` is inferred from `config --hash`; the fault was not switched on for this spike.
- No design above was built or tested; the costs are estimates.

## Reproduce

```bash
# Where the demo's values come from (reads only)
docker compose -f demo/compose.yaml config --no-interpolate | grep -E 'DATABASE_URL|WORKER_BATCH_MB'
env -u SHOP_DATABASE_URL docker compose -f demo/compose.yaml config --hash shop-web
SHOP_DATABASE_URL=postgresql://shop:shop@db:5432/shop docker compose -f demo/compose.yaml config --hash shop-web
docker inspect shop-web --format '{{index .Config.Labels "com.docker.compose.config-hash"}}'
for c in shop-web shop-db shop-worker shop-proxy; do
  docker inspect "$c" --format '{{.Name}} {{index .Config.Labels "com.docker.compose.project.config_files"}}'
done

# Precedence, in a throwaway directory
mkdir -p /tmp/precedence && cd /tmp/precedence
printf 'services:\n  app:\n    image: alpine:3.20\n    env_file: [app.env]\n    environment:\n      FROM_INTERPOLATION: ${DB_HOST:-default-host}\n      BOTH: from-environment\n' > compose.yaml
printf 'DB_HOST=from-dotenv\n' > .env; printf 'DB_HOST=from-env-file-flag\n' > other.env
printf 'BOTH=from-env_file\nONLY_ENV_FILE=from-env_file\n' > app.env
env -u DB_HOST docker compose config app | grep -E 'FROM_|BOTH|ONLY'
DB_HOST=from-shell docker compose config app | grep FROM_
env -u DB_HOST docker compose --env-file other.env config app | grep FROM_
cd - >/dev/null

# The hidden-param rule on the values a fix would need
cd backend && uv run python -c '
from bleep.schemas.graph import PlanStep
from bleep.guard.redact import hides_params
for value in ["postgresql://shop:shop@shop-db:5432/shop", "postgresql://shop-db:5432/shop", "8"]:
    step = PlanStep(action="coolify.set_env", host_id="a", target="shop", params={"key": "K", "value": value})
    print(value, hides_params(step))'
```

Expect `${SHOP_DATABASE_URL:-…}` and `${WORKER_BATCH_MB:-8}`; two different hashes, the first equal to the running shop-web's label when bad-env is off; `from-dotenv`, `from-environment`, `from-env_file`, then `from-shell`, then `from-env-file-flag`; and `True`, `False`, `False`.
