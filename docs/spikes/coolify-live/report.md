# Spike: the Coolify plugin on a real Coolify server

Measured on 2026-11-09 (#337). Step 2 ran with [`spike.py`](spike.py) inside the live backend container and called no model. Step 3 was live incidents on the running Bleep with a real model, on a throwaway application made for this spike.

**Every number below comes from the one saved run of the script (before 10:29 UTC), Coolify's own answers, `docker` on the server over SSH (read only), or the incidents' records (events, usage rows, checkpoints).** Latencies are single calls, not a spread.

> Draft: steps 3 (second round) and 4 are still to run.

## Setup

| | |
|---|---|
| Bleep | `main` at `eb0bf8b`, the image built from it, Docker Desktop on an M-series Mac |
| Coolify | v4.6.0 at `https://coolify.dkwebsite.de`, one server (`localhost`, Traefik proxy) running four applications: three real ones (two Dockerfile apps, one Docker Compose app) and the test app |
| Hosts file | the Coolify server as the SSH host `coolify` (user `root`, key mounted read only) |
| Token | `COOLIFY_TOKEN` in `.env`: the permissions `read`, `deploy` and `write` only, not `root` and not `read:sensitive` |
| Test app | `bleep-testapp` (uuid `8jmqebrstxvyypkrteclkd2r`) in its own project `bleep-spike`: a 60-line Python HTTP server from an inline Dockerfile, with a Dockerfile `HEALTHCHECK` on `/health` every 10 s, 3 retries |
| Model | `openai/gpt-6-luna` through OpenRouter |

### The test app

It answers `/health` with 200 until `TESTAPP_FAIL_AFTER_S` seconds after its start, then with 500, and logs one line when it flips: `ERROR TESTAPP_FAIL_AFTER_S=180 has passed since start: /health answers 500 from now on`. `0` turns the timer off.

A timer, not an env var that breaks it at once, because **Coolify will not leave a broken container running after a redeploy.** It finds the Dockerfile's `HEALTHCHECK` (`custom_healthcheck_found: true`) and gates the rollout on it: a new container that is not healthy fails the deployment, is removed, and the old one keeps serving. A timer lets the new container pass that gate and fail later, which is what an app that breaks at runtime does.

## Step 1: the plugin on

- **Switched on from the dashboard's Plugins page** with `COOLIFY_URL` and `COOLIFY_TOKEN` from `.env` (`PUT /api/plugins/coolify` 200; the settings store holds `{"enabled": true}`).
- **The token reaches the backend without quotes.** `.env` has it in quotes; a raw `cut` of that line sent the quotes along and got 401 when the test app was made, while Docker Compose's `env_file` strips them.
- **The health check's request (`GET /version`) answered `4.6.0` in 153 ms.**

## Step 2: the read tools on the real server

| Call | Latency | Output | Notes |
|---|---|---|---|
| `GET /version` (the health check) | 153 ms | `4.6.0` | |
| `coolify_applications()` | 202 ms | 893 chars | 4 applications, the test app first and marked "(on this host)" |
| `coolify_deployments(app="bleep-testapp")` | 378 ms | 261 chars | one deployment, `finished`, `(api)` |
| `coolify_env_keys(app="bleep-testapp")` | 359 ms | 229 chars | keys and flags, no values |
| `coolify_env_keys(app=<uuid>)` | 373 ms | 229 chars | the same by uuid |
| `coolify_env_keys(app="no-such-app-337")` | 189 ms | 154 chars | "Coolify has no application no-such-app-337; coolify_applications lists them." |
| `coolify_deployments(app="../applications")` | 214 ms | 154 chars | the same sentence; nothing sent with that path |

- **The answers parse.** No call hit "Coolify's answer has an unexpected shape".
- **Every output is guarded.** Each was wrapped once (`<untrusted source="coolify" …>`, the closing tag with the same id) and under the 8,000-char cap; nothing needed redacting.
- **Nothing secret reached an output.** Without `read:sensitive` Coolify sent no webhook secrets, no basic-auth password and no env values (`value`, `real_value`), so there was nothing to drop: 0 such values in the answers, 0 in the outputs. The token appeared 0 times in the outputs.
- **A per-application tool costs two requests**: it lists the applications to find the one named, then reads it. One raw Coolify request took 148-171 ms, so a two-request tool takes about 360 ms.
- **Coolify keeps two rows per env var.** Creating `TESTAPP_BROKEN` made a normal row and an `is_preview: true` copy; `coolify_env_keys` shows both, the copy marked `preview`.

### Shapes against the fixtures

The fixtures (`backend/tests/fixtures/coolify/`) were rewritten by hand from a root token's answers on 2026-11-04. Every field Bleep reads is in the real answers with the same type. The differences are the token's, not wrong shapes:

- **Only in the fixtures:** the application's `manual_webhook_secret_github`, `_gitlab`, `http_basic_auth_password`, `custom_labels` and `docker_compose_raw`; the deployment's `logs`; the env var's `value`, `real_value` and `id`. A token without `read:sensitive` is not shown them (`removeSensitiveData`). The fixtures keep them on purpose: the tests prove Bleep drops them when a root token is used.
- **Only in the real answers:** 69 more application fields (among them `health_check_*`, `ports_exposes`, `restart_count`, `docker_compose_domains`, `custom_healthcheck_found`), 10 deployment fields (`horizon_job_id`, `current_process_id`, …) and 12 env var fields (`comment`, `order`, `version`, `is_required`, `is_shared`, `is_multiline`, …). Bleep ignores them.

### Found

- **An application with no git source shows Coolify's placeholder repo (#354).** The test app, built from an inline Dockerfile, read `repo=coollabsio/coolify@main commit=HEAD`. Coolify fills `git_repository`, `git_branch` and `git_commit_sha` with its defaults when `source_type` is null. Every application also showed `commit=HEAD`: `git_commit_sha` is the ref Coolify deploys, not a commit.
- **A Docker Compose application shows `domains=none` (#355).** Coolify leaves `fqdn` null for it and keeps the domains per Compose service in `docker_compose_domains`, which Bleep does not read.

## Step 3: break the test app, let Bleep handle it

### Round 1 (10:31-10:43 UTC)

1. **The test app was changed and deployed with `TESTAPP_FAIL_AFTER_S=180`.** A `PATCH` of its `dockerfile` (base64) answered 200; `ports_exposes` was sent along as `8080`, since Coolify reads the port from the still base64-encoded Dockerfile and would fall back to 80. `POST /deploy` queued deployment `cjlxyaopqoxaxsntaeyudip6` at 10:31:16; it was `finished` at 10:32:08 (52 s, polled every 10 s). The new container started at 10:31:24 and was healthy.
2. **Docker marked it unhealthy at 10:34:50**, 3 minutes after the start plus three failed checks. **The watcher opened the incident at 10:34:55** (`unhealthy on coolify/8jmqebrstxvyypkrteclkd2r`).
3. **Investigate used a Coolify tool on its own.** Four tool calls: `list_containers`, `inspect_container`, `recent_events`, `coolify_deployments`. It did not call `coolify_env_keys`.
4. **Diagnose was right and sure, but dropped the variable's name (#357).** `config`, confidence 0.99: "The app is configured to fail its own health check after 180 seconds". The logs it had held `TESTAPP_FAIL_AFTER_S=180` twice. Propose sees only the diagnosis and answered **no action**: "The diagnosis does not identify the setting's name or a supported replacement value, so I can't safely change it through Coolify." Its instructions to the human were right ("change the configuration that enables the 180-second fail-after behavior, then redeploy").
5. **Nothing asked for approval; the incident closed at 10:35:26**, 31 s after it opened. 7 model calls, 30,025 tokens in, 1,826 out, **$0.0028**.

**From outside, the URL answered 404, not the app's 500.** Traefik stops routing to a container Docker marks unhealthy, so the public URL said "404 page not found". The app's own 500 is only seen inside the container.

**Then Bleep looped (#356).** A URL check on the test app's `/health` was added on the dashboard at about 10:35. From 10:36 to 10:43 a new incident opened every minute for the same failure, 7 in all (`url failed on coolify/… (seen 7 times since 09 Nov 10:36 UTC)`), each running the whole graph, proposing no action, closing after about 50 s and sending a Telegram message. The detector opens an incident after `url_failures_to_open` failures in a row and forgets the incident that just ended; the fold counts the repeats but does not stop them. The diagnoses varied: four named `TESTAPP_FAIL_AFTER_S=180` (confidence 0.48-0.55, unsure because the URL said 404 and the logs 500), two blamed Coolify's Caddy labels on a Traefik server (0.76, 0.77, wrong). Each proposed no action. The loop cost about $0.033 and 10 Telegram messages; it stopped when the backend was stopped at 10:43:41. The test app was then set back to `TESTAPP_FAIL_AFTER_S=0` and redeployed by hand.

**`PATCH /applications/{uuid}/envs` `{key, value}` answered 201, not 200, and updated only the normal row**; the preview copy kept its value. Bleep's client takes any 2xx as success (`_call`), so `set_env` reports "updated", and a production deploy reads the normal row, so this is right for Bleep.

## Cost

| | |
|---|---|
| OpenRouter usage before | $7.6272 |
| After round 1 and the loop | $7.6629 |
