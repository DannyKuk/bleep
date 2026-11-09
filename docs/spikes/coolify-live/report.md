# Spike: the Coolify plugin on a real Coolify server

Measured on 2026-11-09 (#337). Step 2 ran with [`spike.py`](spike.py) inside the live backend container and called no model. Step 3 was live incidents on the running Bleep with a real model, on a throwaway application made for this spike.

**Every number below comes from the one saved run of the script (before 10:29 UTC), Coolify's own answers, `docker` on the server over SSH (read only), or the incidents' records (events, usage rows, checkpoints).** Latencies are single calls, not a spread.

> **Answer:** yes, after three fixes the spike found. The read tools parse the real answers and drop nothing secret, because a least-privilege token is shown none. Bleep found a runtime fault in a Coolify app, read the app's README for the safe value, planned `coolify.set_env TESTAPP_FAIL_AFTER_S=0` and `coolify.redeploy`, ran them after the person's yes, waited for the deployment (61 s), and verified the app and its URL: fixed and verified, 2 min 9 s from the incident to the report, $0.0033. A plan naming a protected application, by name or by uuid, is refused before approval. On the way, two live rounds proposed no action and one looped: Diagnose dropped the variable's name (#357), Investigate never read the docs for the value (#364), and a failure that outlived a no-action incident opened a new incident every minute (#356) or a second one by another signal (#366). All four are fixed and merged. Open: #354, #355, #365.

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

**Then Bleep looped (#356).** A URL check on the test app's `/health` was added on the dashboard at about 10:35. From 10:36 to 10:43 a new incident opened every minute for the same failure, 7 in all (`url failed on coolify/… (seen 7 times since 09 Nov 10:36 UTC)`), each running the whole graph, proposing no action, closing after about 50 s and sending a Telegram message. The detector opens an incident after `url_failures_to_open` failures in a row and forgets the incident that just ended; the fold counts the repeats but does not stop them. The diagnoses varied: four named `TESTAPP_FAIL_AFTER_S=180` (confidence 0.48-0.55, unsure because the URL said 404 and the logs 500), two blamed Coolify's Caddy labels on a Traefik server (0.76, 0.77, wrong). Each proposed no action. The loop cost $0.0348 and 10 Telegram messages; it stopped when the backend was stopped at 10:43:41. The test app was then set back to `TESTAPP_FAIL_AFTER_S=0` and redeployed by hand.

**`PATCH /applications/{uuid}/envs` `{key, value}` answered 201, not 200, and updated only the normal row**; the preview copy kept its value. Bleep's client takes any 2xx as success (`_call`), so `set_env` reports "updated", and a production deploy reads the normal row, so this is right for Bleep.

### Round 2 (11:31-11:48 UTC), with #356 and #357 merged

The setup changed in two ways. The app's configuration is documented the way a real app's would be: a README in a linked repo (`DannyKuk/bleep-shop-live`, `testapp/README.md`; `repo` and `docs_paths: [testapp]` on the service in the hosts file) says `TESTAPP_FAIL_AFTER_S` "`0` turns it off. Production must run with `0`". It was in knowledge search as 3 chunks 3 s after the backend restarted. The app's log line stayed as in round 1.

1. **Deployed with `TESTAPP_FAIL_AFTER_S=180`** at 11:31:02; `finished` at 11:31:53 (51 s). The container started at 11:31:06.
2. **The watcher opened the incident at 11:34:40** (`inc_01M701DHAF1JHXABXB`).
3. **Investigate made 2 tool calls** (`list_containers`, `inspect_container`) and stopped: the logs in the context already explained the cause. It did not search the docs.
4. **Diagnose named the key** (#357): "The bleep-testapp container has TESTAPP_FAIL_AFTER_S=180, which makes its /health endpoint return HTTP 500 after 180 seconds", `config`, confidence 0.99.
5. **Propose still answered no action:** "The available Coolify action can set a value but cannot remove the variable, and no safe replacement value is established by the diagnosis." Its instruction to the human named the key: "remove the TESTAPP_FAIL_AFTER_S environment variable (currently 180), then redeploy". The safe value was in the README, which no step read (#364). The incident closed at 11:35:05 without asking the person, though the fix was within the enabled actions (#365).
6. **The URL check opened one more incident** at 11:35:52 (`url failed …`, a different signal kind, so not held), which ended the same way at 11:36:35. **Then nothing more opened**: the URL kept failing until the app was set back at 11:48, and #356's hold kept it from opening again. One broken service still cost two runs and three Telegram messages (#366).
7. **Diagnose flagged `planted_in: ["memory"]`** though the remembered incidents (this morning's, with causes and "nothing ran") held no orders. The report did not pass it on (`planted_in: []`), so the person was not told of planted text that was not there.

The two incidents cost $0.0077: the first 6 model calls and $0.0027, the URL incident 9 calls and $0.0050.

### Round 3 (12:23-12:32 UTC), with #364 and #366 merged

1. **Deployed with `TESTAPP_FAIL_AFTER_S=180`** at 12:23:35, `finished` at 12:24:27. The URL check had passed after the backend restarted, so the failure counted as new.
2. **The incident opened at 12:27:10** (`inc_01M704DNQCK5DRWSFP`).
3. **Investigate made 6 tool calls:** `list_containers`, `inspect_container`, `recent_events`, `coolify_applications`, **`knowledge_search("TESTAPP_FAIL_AFTER_S", sources: repo)`** and `coolify_deployments`. The search returned the README.
4. **Propose planned the fix** (12:27:41): `coolify.set_env bleep-testapp TESTAPP_FAIL_AFTER_S=0`, then `coolify.redeploy bleep-testapp`, "Set the documented health-check setting to 0 and redeploy so the running container receives the corrected environment. A restart alone would preserve the failing value." Verification: the URL only; Propose drops the containers of an application it redeploys, since their names change.
5. **The person approved it on the dashboard** at 12:27:54.
6. **Execute:** `set_env` done in 1 s (Coolify answered 201 and updated the normal row). `redeploy` queued deployment `vyx6rxfkske5gyxycxwlvkwg` and **waited for it: done at 12:28:56, 61 s**; Coolify records it as created 12:27:55 and finished 12:28:50.
7. **Verify passed on its first check** at 12:29:17, 20 s after: "bleep-testapp (Coolify application): 1 running" and the URL "answering 200". It checked the application as a whole, so the new container's new name did not matter.
8. **Report** at 12:29:19: "fixed and verified".
9. **Checked from outside at 12:32:30**, past the old 3-minute mark: the new container `8jmqebrstxvyypkrteclkd2r-20261109T122755` (started 12:27:59) healthy for 4 minutes, its log saying `TESTAPP_FAIL_AFTER_S=0`, the URL answering 200.
10. **One incident and no second one:** the URL failed while the incident was open and attached to it (#366).

7 model calls, 34,102 tokens in, 2,084 out, **$0.0033**.

## Step 4: protected applications

[`protect.py`](protect.py) ran in the backend container after round 3. It read the Coolify server over SSH (read only, 11 containers, 0.9-1.3 s to connect and read) into a registry built from the hosts file, added a protection for the real application `molecula` (uuid `hfys2nogpz7wrhcoqxymnfkt`, a Docker Compose app with three containers) in memory, and ran `check_plan` on plans naming it. No model was called and nothing was sent to Coolify.

| Plan | Not protected | Protected by name `molecula` | Protected by uuid |
|---|---|---|---|
| `coolify.redeploy molecula` | passes | refused, `protected` | refused, `protected` |
| `coolify.redeploy hfys2nogpz7wrhcoqxymnfkt` | passes | refused, `protected` | refused, `protected` |
| `coolify.set_env molecula` + `coolify.redeploy molecula` | refused, `hidden_param` (see below) | both steps refused, `protected` | both steps refused, `protected` |
| `docker.restart` of one of its containers | refused, `healthy_target` | refused, `protected`: "belongs to the Coolify application molecula, which is protected" | refused, `protected` |
| `coolify.redeploy bleep-testapp` (control) | passes | passes | passes |

- The refusal names the application as the entry protects it ("the Coolify application molecula" or "… hfys2nogpz7wrhcoqxymnfkt").
- The unprotected set_env was refused because the spike's made-up key `SPIKE_KEY` names a secret, so its value would be shown redacted and the person could not read what they approve (`check_shown`, #294). That is the rule working.
- **#140 was not tested:** Bleep is not deployed through Coolify here, so there is no `bleep.self=true` container of a Coolify application to hide.

## Cost

| | |
|---|---|
| Spike incidents (11, from the usage ledger) | $0.0486 |
| of which the round-1 loop (#356) | $0.0348 |
| Round 3, the fix | $0.0033 |
| Steps 1, 2 and 4 | no model calls |

The eval rounds of the fixes found here (#357 $0.0299, #364 $0.0266) are in their own reports.

## What changed on the server

Only the test application and its project `bleep-spike`, both made for this spike the same morning: its Dockerfile (a PATCH), the env var `TESTAPP_FAIL_AFTER_S` (two rows, the normal one and Coolify's preview copy) and six deployments (five by hand at 10:31, 10:44, 11:31, 11:48 and 12:23, one by Bleep at 12:27). The other applications' containers were only read. The test application stays for the walkthrough, healthy with `TESTAPP_FAIL_AFTER_S=0`; how to delete it is in the setup notes kept outside the repo.
