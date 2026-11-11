# Setup

## Running Bleep

The [README's quick start](../README.md#quick-start) runs Bleep with the demo profile, which lets it watch the Docker engine of the machine it runs on through a filtered socket proxy ([why](decisions/socket-host.md)), and the demo shop for it to watch ([Demo](demo.md)). `BLEEP_ADMIN_PASSWORD` needs at least 12 characters, or Bleep refuses to start; a random `BLEEP_SECRET`: `openssl rand -hex 32`.

Without the `demo` profile only the backend and the dashboard start, and the hosts file has to name a host you reach some other way.

The data (the SQLite database, and the secret file below) lives in the `bleep-data` volume. `docker compose down` keeps it; `docker compose down -v` deletes it.

### Keys and tokens: from the dashboard or `.env`

Only `BLEEP_ADMIN_PASSWORD` and `BLEEP_SECRET` must be in `.env`: the dashboard is behind them. Every other key, token and plugin address can be set in either of two ways.

1. **From the dashboard.** Open Settings: the Secrets panel and each plugin's card have a row per key, token and address, with where it is set (the dashboard or `.env`), an input to set it and a button to clear it. Type the value and save: it goes to Bleep's `secrets.json` on the `bleep-data` volume, readable only by the backend's user, never in the database, and is used from the next call without a restart. A value is never shown again, prefilled or sent back: only whether it is set (of a plugin address, only its `https://host`). A value set in `.env` shows as "Set in .env" with no input, since `.env` wins. A new or cleared plugin address clears its token and switches the plugin off; a token has no input until its address is set. An address that no longer passes the checks (the file was edited by hand) shows as "Set, but not valid": enter it again.
2. **In `.env`**, then restart the backend (`docker compose up -d backend`). A value in `.env` wins over the dashboard's and cannot be changed or cleared from it, which suits Compose files and CI.

**Back up the volume like `.env`:** a copy of `bleep-data` holds these secrets in clear. Details: [Settings](design/settings.md#secrets-from-the-dashboard-135).

### Models, embeddings and tracing: from the dashboard or `.env`

The model (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_CHEAP_MODEL`, `LOCAL_MODEL`), the embeddings (`EMBEDDINGS_PROVIDER`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_MIN_SCORE`) and tracing (`TRACING`, `LANGFUSE_BASE_URL`, `LANGFUSE_UI_URL`, `LANGFUSE_PROJECT_ID`, `LANGSMITH_ENDPOINT`, `LANGSMITH_PROJECT`) can be set on the dashboard's Settings page (the Models, Embeddings and Tracing panels, #433) as well as in `.env`; a field the environment sets shows "Set in the environment (.env or Compose)" there, with no input. `LOCAL_BASE_URL` and `EMBEDDINGS_BASE_URL` are set like the keys above, in the Models and Embeddings panels next to the settings they belong to, and from the dashboard they may be a private address or `host.docker.internal`, where a local model usually listens; `EMBEDDINGS_API_KEY` is shown only while the embedder in force is an endpoint; the built-in model and a local server need no key. A model or tracing change applies at once, except a change to a running Langfuse tracer; the embeddings apply at the next start, and the page asks before it saves them (a provider or model change embeds the memory and the knowledge index again then) and says "A restart is needed" until Bleep has restarted. As for keys, a value set in the environment wins: leave the line empty to choose it from the dashboard (`.env.example` leaves them all empty). The environment is `.env` and what a Compose file sets: `docker-compose.observability.yaml` sets `TRACING` (to `langfuse` unless `.env` says otherwise), the Langfuse keys, `LANGFUSE_BASE_URL`, `LANGFUSE_UI_URL` and `LANGFUSE_PROJECT_ID`, so with that file those are changed in `.env` or the file, not on the dashboard.

- The model choice, `LOCAL_BASE_URL` and LangSmith tracing apply at once; so do switching Langfuse on, switching it off and clearing one of its keys. A change to a running Langfuse tracer's keys, addresses or project applies at the next start, and so does switching it on again with a key it had before: `GET /api/settings` lists `tracing` in `restart_needed` until then, because Langfuse keeps one client per key for the life of the process.
- The embeddings provider and model apply at the next start, which re-embeds the memory; the min score applies at the next start too, without re-embedding. `GET /api/settings` lists `embeddings` in `restart_needed` until then.
- `EMBEDDINGS_BASE_URL` and `EMBEDDINGS_API_KEY` come from one place: when one is in `.env`, so is the other, and a key left on the dashboard is not used (the log says so at start). A new address set on the dashboard clears its key. The address cannot be cleared while the endpoint is the running embedder: choose the built-in model and restart first. The same holds for a local server.
- A tracer's address (`LANGFUSE_BASE_URL`, `LANGSMITH_ENDPOINT`) set on the dashboard must be https to a public address, and changes only while its keys are unset: clear the key, set the address, enter the key again.

Details: [Settings](design/settings.md#models-embeddings-and-tracing-from-the-dashboard-372).

### A first incident, end to end

This runs the whole path from a dead container to the approval question, on the dashboard and in Telegram. It needs a model key and, for the Telegram half, a bot.

1. On the dashboard or in `.env` ([above](#keys-and-tokens-from-the-dashboard-or-env)): `OPENROUTER_API_KEY` (or `LLM_PROVIDER=local` with `LOCAL_BASE_URL` and `LOCAL_MODEL`, [below](#models-embeddings-and-tracing-from-the-dashboard-or-env)), and for Telegram `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (send the bot a message first, then read the chat id from `https://api.telegram.org/bot<token>/getUpdates`).
2. Start Bleep and a throwaway container for it to watch, with no restart policy:
   ```bash
   docker compose --profile demo up -d --build
   docker run -d --name bleep-try --restart no nginx:1.27-alpine
   ```
   Log in at http://localhost:3000. Within about 30 s the overview lists `bleep-try` in the Demo stack's tile as OK.
3. Break it: `docker kill bleep-try`. Use `kill`, not `stop`: a `docker stop` (exit 0 or 143, a `stop` event) is someone's choice and opens nothing, by design ([watcher](design/watcher.md)). A kill leaves exit 137, the same as a crash, and the restart policy `no` keeps it down.
4. What to expect:
   - **Overview:** the service turns red (Down) at the next tick and the Demo stack's tile is tinted. After the 60 s grace window it is still down, so an incident opens and the service's row gets an "Incident" link. Once the plan is ready, the top of the overview says "bleep-try is down. I know why, and I have a fix." with the plan in one line and "Show me the plan". Allow about 75 s from the kill.
   - **Incident page** (the link, or Incidents in the menu): the badge at the top right says Live. The timeline fills in (Gathered context, Investigating), and "How I got there" lists the tool calls as they happen, each as a plain sentence with the tool's name and arguments in mono, for example "I looked closely at a container" with `inspect_container` and `container=bleep-try`, and its result folded under it. Then "What I found" shows the cause (killed, exit 137, restart policy `no`), and "Here is my plan" a plan such as Start `bleep-try` (`docker.start` on `demo`) with "Yes, go ahead", "No, leave it" and "Tell Bleep something". The status pill says Needs your yes, and the progress bar under the headline stands at Your yes.
   - **Telegram:** the same plan arrives as a message with Approve and Reject buttons; a reply to it is offered to the plan as an instruction, and Send sends it.
5. Answer on one side, then try the other. The dashboard locks its buttons and says, for example, "You said yes on the dashboard, …" (on a phone the bar at the bottom of the screen becomes that one line); a tap in Telegram afterwards is told the dashboard already decided. Approve in Telegram first instead, and the page shows "You said yes on Telegram, …" with its buttons locked; a page that has not caught up yet answers a click with "Already decided on Telegram".
6. Clean up: `docker rm -f bleep-try`.

### Approve, run, verify and report

This continues past the approval: Bleep restarts what is down, checks the service and writes the report. It needs the same model key (and the bot for the Telegram half).

1. With Bleep running as above, start a small stack whose web container depends on a database, both with no restart policy and the two labels that make Bleep group them as one Compose project, so the overview shows one service. Bleep treats a container as part of a Compose project only when it carries `com.docker.compose.config-hash` as well as the project label, because `docker compose build` puts the project label on the image and a plain `docker run` of that image would inherit it ([why](design/hosts.md)):
   ```bash
   docker run -d --name bleep-try-db --restart no --label com.docker.compose.project=bleep-try --label com.docker.compose.config-hash=demo redis:7-alpine
   docker run -d --name bleep-try-web --restart no --label com.docker.compose.project=bleep-try --label com.docker.compose.config-hash=demo nginx:1.27-alpine
   ```
   Within about 30 s the overview lists the `bleep-try` service as OK; its page shows both containers running.
2. Stop the dependency the way a crash does: `docker kill bleep-try-db` (a `docker stop` opens nothing, by design). After about 75 s an incident opens, and "Here is my plan" shows a plan such as Start `bleep-try-db` (`docker.start` on `demo`).
3. Press "Yes, go ahead" on the dashboard. What to expect:
   - **Plan card:** each step's number turns into a spinner, then a check; then the checks show as pills, green when they pass.
   - **Timeline:** "Approved on the dashboard", "Ran the plan" with `docker.start bleep-try-db: started` and `docker.start bleep-try-db: done` under it, then "Checking the service" for about 20 s and "Check 1 passed" (a failed check is followed by up to two more, 10 s apart).
   - **Report card:** the outcome "Fixed and verified", the action taken `docker.start bleep-try-db on demo: done`, "Approved on the dashboard" and the timeline. The incident's status is Resolved.
   - **Telegram:** the verification result and then the report arrive in the chat.
   - `docker ps` shows `bleep-try-db` running again, with a new start time.
4. Optional, the stale-plan refusal: kill the database again, wait for the next proposal, and before saying yes run `docker start bleep-try-db` yourself. Then press "Yes, go ahead": the timeline says "The plan did not run, the state changed since approval: bleep-try-db on demo was started again. Planning again.", nothing is restarted, and a new plan is proposed for the container as it is now (usually no action, since it is running).
5. Clean up: `docker rm -f bleep-try-db bleep-try-web`.

If the page stays on Connecting, or the reasoning only appears when the run ends, the stream is being buffered between the browser and the dashboard ([why it can happen](decisions/sse-through-next.md)).

## Real hosts

An SSH host can be added in either of two ways. A socket host (`unix://` or `tcp://`) is set in the hosts file only: it needs no key, so the dashboard cannot prove the person adding it may use that engine ([why](design/hosts.md#hosts-from-the-dashboard-194)).

### From the dashboard

Bleep keeps the host's address in its database and the private key and the host key you confirm in `secrets.json` on the `bleep-data` volume, never in the database, and starts watching the host at once, without a restart.

1. On the overview, press the + next to "Your hosts" (or "Add a host" when there are none yet); Settings has the same form under "Hosts", "Add an SSH host". Fill in a name, the address (a host name or IP, never loopback or link-local), the port, the user and the Docker socket path. Paste the private key into the key box. Bleep never shows it again and empties the box once the host is saved.
2. Press "Check the host key". Bleep fetches the host key the server offers and shows its fingerprint (`SHA256:...`). Nothing is saved yet. On the server, `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` (or the key file of the type shown) must print the same fingerprint; if it does not, stop. Tick that it matches. Any failure of the check says "Could not get an SSH host key from that address." (the backend log names the kind of error), and only one check runs at a time.
3. Press "Add host" within ten minutes of the check. After ten minutes, or after the address or port changes, the page asks you to check again.

The host appears on the overview within a few seconds. The host's page (click its name on the overview) shows its key and host key only as "set" or "missing", and is where it is edited or removed. Hosts on the machine Bleep runs on (loopback) and socket hosts cannot be added there; they go in the hosts file.

To change a host, use "Edit connection". **A new address, port or user clears the key and the host key**, and the page says so: check the host key and enter the key again; Save stays disabled until both are done. When the server's host key changes, Bleep refuses to connect; check the new key's fingerprint and save it from the same form. If the host was changed elsewhere since the page read it, the save is refused with "This host changed since this page read it; reload the page." "Remove" asks a second time ("Yes, remove this host"), stops watching the host, deletes its keys, and closes its open incidents with a notice.

### In the hosts file

A host the hosts file names is shown on the dashboard as set in the hosts file and cannot be changed or removed there; the file wins.

1. Copy the example and edit it: `cp config/hosts.example.yaml config/hosts.yaml`. The file is gitignored. Every field is documented in the example.
2. Put the SSH private key and a `known_hosts` file in `./secrets` (also gitignored), and point the hosts file at them as `/run/secrets/...`:
   ```bash
   mkdir -p secrets/ssh
   cp ~/.ssh/bleep_deploy_key secrets/ssh/id_ed25519
   ssh-keyscan -H your-host >> secrets/ssh/known_hosts
   ```
   Keep the key at mode 600, and make the backend's user able to read it. The backend runs as uid and gid 10001, fixed in its image.
   - **Linux:** `sudo chown 10001:10001 secrets/ssh/* && chmod 600 secrets/ssh/*`. Without the chown, a 600 key owned by your own user is unreadable inside the container.
   - **Docker Desktop on macOS:** the bind mount maps ownership to the container's user, so `chmod 600 secrets/ssh/*` is enough.
   `./secrets` is mounted read-only at `/run/secrets`. Check the fingerprint `ssh-keyscan` returned against one you trust before relying on it.
3. In `.env`, set `BLEEP_HOSTS_FILE=/config/hosts.yaml`. When it is unset, compose uses `/config/hosts.demo.yaml`.
4. `docker compose up -d --build` (without the `demo` profile if you don't want the local engine watched).

Secrets inside the hosts file are references, `{env: NAME}` or `{file: PATH}`, never values. An environment variable reaches the backend from `.env`.

### What Bleep requires of an SSH host

- **Host keys are always verified, and it fails closed.** A host added on the dashboard is checked against the one host key you confirmed there and nothing else. For a hosts file host, Bleep checks the `known_hosts_file` of the host if set, else `~/.ssh/known_hosts` inside the container. With neither, or with a key that doesn't match, it does not connect. There is no setting that turns checking off ([design](design/hosts.md)).
- **Encrypted private keys are not supported**, since there is no ssh-agent in the container. Use a dedicated, unencrypted deploy key for Bleep, give it to Bleep only, and revoke it by removing it from `authorized_keys`.
- **The SSH user needs access to the Docker socket, which is root-equivalent.** On Linux that means the user is in the `docker` group, or is root. Anyone who can use that key can take over the host. Treat the key like a root password: a dedicated key, a dashboard on a trusted network only, and nothing else sharing the secrets directory.
- **Docker Desktop on macOS** keeps its socket in the user's home, so the host needs `docker_socket: /Users/<user>/.docker/run/docker.sock`. The remote login also has to be on (System Settings, Sharing, Remote Login). How the socket is forwarded: [Docker over SSH](decisions/docker-over-ssh.md).
- **A laptop that sleeps** gets `may_sleep: true` in the hosts file, or "This host may sleep" on its page: while it does not answer it shows as asleep and opens no incident ([why](design/watcher.md#bleeps-own-sleep-hosts-that-may-sleep-and-recovery-85)).

## Network and cookies

The dashboard (port 3000) is for a trusted LAN only. It has one admin password, a limit of 5 failed logins a minute shared by every browser ([app](design/app.md)), and nothing more; it is not hardened for the internet. The backend's port 8000 is published on `127.0.0.1` only, so other machines reach Bleep through the dashboard, which forwards `/api` to the backend.

The watcher checks every host every 15 s; `BLEEP_WATCH_INTERVAL_S` in `.env` changes that. When the machine Bleep runs on wakes from sleep, a host that does not answer is not counted for the next 4 checks (`BLEEP_WAKE_GRACE_TICKS`), so the network can come back first.

The dev panel (prompts, budgets, cost per incident and per day, the guard log) is off; set `BLEEP_DEV_PANEL=true` in `.env` and restart to get it under Dev in the dashboard ([dev panel](design/dev-panel.md)).

The session cookie is not `Secure` by default, because the dashboard is plain http on the LAN. If you put it behind https, set `BLEEP_COOKIE_SECURE=true` in `.env`.

## Telegram (optional)

Without it everything works from the dashboard. To also get incidents and approve from your phone:

1. Create a bot with BotFather and set its token as `TELEGRAM_BOT_TOKEN`, on the dashboard or in `.env` ([how](#keys-and-tokens-from-the-dashboard-or-env)).
2. Send the bot any message, then read your chat id from `https://api.telegram.org/bot<token>/getUpdates` (`result[0].message.chat.id`) and set it as `TELEGRAM_CHAT_ID` the same way.
3. Set on the dashboard, the bot starts polling at once; in `.env`, restart Bleep. A proposal arrives with Approve and Reject buttons; reply to it, or to any message of the incident, to give an instruction instead: Bleep asks "Send this to shop #14's plan?" and only Send sends it. Ask it in your own words to snooze or leave a service alone, watch it again or mark an incident as expected, and it answers with one button to confirm.
4. Optional: set `BLEEP_DASHBOARD_URL` in `.env` to the address your browser uses for the dashboard (`http://192.168.1.5:3000`), and restart; the last message of every incident then links to its page. A `localhost` address works only on the machine Bleep runs on.

Each incident is one thread: its first message says 🔴 New (or 🔁 Again for a repeat) with a short name such as `shop #14`, every later one replies to it, and the last one leads with how it ended. The first message has a 🤫 I'm on it button: it snoozes the service (or the host) for 1 h, 4 h or until 08:00 and ends the incident. Choose your time zone under Settings, Time zone, so that 08:00 and the times in messages and on the dashboard are yours; it applies to the next message without a restart. `BLEEP_TIMEZONE` in `.env` (`Europe/Vienna`) is the starting value until one is chosen there. With neither, Telegram's times are UTC and say so, and the dashboard shows the browser's zone (#400).

Only that chat is answered. Messages and button taps from any other chat are ignored and recorded in the guard log. The first answer wins, from Telegram or the dashboard; the other is told who won. Details: [Notifications](design/notify.md).

## Exporting the chat

What was said to Bleep, in the dashboard's chat panel and in Telegram, can be saved with how Bleep read each message: "Export chat" (the arrow in the panel's header) downloads it as Markdown. It holds the last 100 messages of each chat since Bleep last started, the person's words as Bleep read them, with secrets redacted ([what is in it](design/notify.md#talking-to-bleep-390)). From a shell, log in for a session cookie and fetch the same file (`format=md`; without it, JSON):

```bash
curl -c bleep-cookies -H 'content-type: application/json' -d '{"password": "<BLEEP_ADMIN_PASSWORD>"}' http://localhost:3000/api/auth/login
curl -b bleep-cookies -OJ 'http://localhost:3000/api/talk/log?format=md'
```

## GitHub token (optional)

Bleep reads each service's repo (the `repo` of a service in the hosts file): its recent commits and their diffs while it investigates, and, for knowledge search, its README (unless the service sets `readme: false`) and the markdown under the paths in `docs_paths` (the `docs` folder when it names none). Without a token it reads public repos only, at 60 requests an hour.

1. Create a **fine-grained** personal access token on GitHub (Settings, Developer settings, Personal access tokens, Fine-grained tokens), limited to the repos the hosts file names.
2. Give it two repository permissions, both **read-only**: **Contents** (commits, diffs and files) and **Metadata** (GitHub adds it to every token). Nothing else: Bleep never writes to GitHub.
3. Set it as `GITHUB_TOKEN` on the dashboard (used from the next request) or in `.env` (then restart). The hosts file points at it with `github: token: {env: GITHUB_TOKEN}` (as in `config/hosts.example.yaml`), or has no `github` section; a hosts file that names another variable or a file is read at start instead. Unset, Bleep reads public repos only.

## Plugins (optional)

A plugin adds the tools of one more system, such as a Coolify instance. Each one is off until you switch it on. For each:

1. Set its address, then its token, on the dashboard ([how](#keys-and-tokens-from-the-dashboard-or-env)); or set both in `.env` and restart the backend. An address set on the dashboard must be https and reach public addresses only; a plugin on your own network or over plain http (such as `http://coolify:8000`) is set in `.env`. An address and its token come together from one place: when `.env` sets either, both are read from `.env`.
2. In the dashboard, open Settings, find the plugin under Plugins and press **Switch on**. Its secrets show only as set or missing; a refusal names the variable that is missing or the setting that is wrong, and why.

**A new address clears the token.** Setting or clearing a plugin's address on the dashboard deletes the token stored for it and switches the plugin off, so a token is never sent anywhere it was not entered for: enter the token again and switch the plugin on. A new token for the same address is used at once.

A plugin switched on whose token is later unset shows **On, waiting for** that variable: it keeps your choice and runs again once the variable is back (at once from the dashboard, after a restart from `.env`), and **Switch off** turns it off for good. Details: [Plugins](design/plugins.md).

### Coolify

- `COOLIFY_URL`: where Coolify answers, such as `https://coolify.example.com`. It must be https unless it is a loopback or private address (`127.x`, `10.x`, `172.16.x` to `172.31.x`, `192.168.x`, Tailscale's `100.64.x`, IPv6 `::1` and `fc00::/7`) or a single-label name such as a Docker service, since the token travels with every request.
- `COOLIFY_TOKEN`: an API token from **Keys & Tokens > API tokens** with the `read`, `deploy` and `write` permissions only. Never `root` and never `read:sensitive`: without `read:sensitive` Coolify leaves env values, deployment logs and other secrets out of what it answers.

Its one setting, `deploy_timeout_s`, is how long a redeploy may take (default 900 s).

### MCP server by URL

- `MCP_URL`: the server's Streamable HTTP endpoint, required.
- `MCP_TOKEN`: its bearer token, used when set. Give it the fewest rights the server offers: on a server that scopes its tokens, as Coolify's does, a read-only token keeps it read-only whatever Bleep's settings allow.

In its settings, write in one line what the server is for (the plugins page's *Purpose*, at most 200 characters; Investigate tells the model, so it knows when asking the server is worth a call) and list the server's tools Bleep may run: `read_tools` without approval, `act_tools` only in a plan you approve. Any other tool is not run. An example with Coolify's own MCP server: [Plugins](design/plugins.md#mcp-server-by-url).

## Memory embeddings (optional)

Bleep remembers every finished incident and recalls similar ones. The Settings page's Embeddings panel (or `EMBEDDINGS_PROVIDER` in `.env`) offers three embedders:

| Provider | What it is | Pick it when |
|---|---|---|
| `local` (the default, "built-in") | bge-small inside the backend image: about 11 ms per incident on a laptop, no key, no network, nothing to run | your logs and docs are in English, or you want nothing to set up |
| `server` ("local server") | bge-m3 or another model on LM Studio, Ollama or any OpenAI-compatible server on this machine or the network: no key, nothing leaves your network, free | they are in German or mixed languages and a machine can keep the server running next to Bleep |
| `openai` ("endpoint") | an OpenAI-compatible service with a key, such as OpenRouter's `google/gemini-embedding-2` | they are in German or mixed languages and there is no machine for a server; a few cents and a key are fine |

A local server must be running whenever Bleep embeds: with it stopped, recall and knowledge search fail as an unreachable endpoint does (the incident run goes on without them). **A local server (LM Studio):** download `bge-m3` (in LM Studio's model search, or `lms get https://huggingface.co/gpustack/bge-m3-GGUF@FP16 --gguf`), start the server (`lms server start`, or the Developer tab; LM Studio's usual port is 1234 and it shows the one it uses) and set, in `.env` or on the dashboard:

```bash
EMBEDDINGS_PROVIDER=server
EMBEDDINGS_BASE_URL=http://host.docker.internal:1234/v1   # from the backend container, the host is host.docker.internal
EMBEDDINGS_MODEL=text-embedding-bge-m3                    # the id LM Studio lists at /v1/models
```

`docker-compose.yaml` already maps `host.docker.internal` to the host for the backend, on Linux too. bge-m3 on LM Studio put the right chunk first for 22 of 22 queries over sloppy German docs (bge-small 18) and took 15 s for the first ingest of the Docker docs; recall's cut-off is 0.35 for it on its own, also under LM Studio's name `text-embedding-bge-m3` ([local server](evals/embeddings-local-server-2026-11-11.md), #442). Ollama's `bge-m3` is the same API (`http://host.docker.internal:11434/v1`, model `bge-m3`) and was not measured. The same address field serves an endpoint, so changing between the two means changing it.

To use an OpenAI-compatible endpoint with a key instead (OpenAI, OpenRouter, or a server that wants one), set in `.env`, or the same values from the dashboard ([above](#models-embeddings-and-tracing-from-the-dashboard-or-env)); the address and its key come from one place, since the key is sent there:

```bash
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_BASE_URL=http://host.docker.internal:1234/v1   # the server's OpenAI-compatible base URL
EMBEDDINGS_MODEL=text-embedding-nomic-embed-text-v1.5     # a model that server offers
EMBEDDINGS_API_KEY=                                        # only if the server wants one
EMBEDDINGS_MIN_SCORE=                                      # recall's cut-off, 0 to 1; unset: the model's own
```

With the provider set and the URL or the model missing, the built-in model embeds and the log says so. After a model change, the next start embeds the stored incidents again; until that is done they are left out of recall. The same embedder indexes the docs for knowledge search. For logs and runbooks mostly in another language, a multilingual model behind a local server or an endpoint is the option: `BAAI/bge-m3` found German questions' pages first twice as often as bge-small, English ones less often, and is too heavy to ship in the image ([bge-m3 against bge-small](evals/embeddings-multilingual-2026-11-06.md)). Its scores sit lower than bge-small's, so recall cuts off at 0.35 for a model named `bge-m3` instead of 0.5 ([min score](evals/embeddings-min-score-2026-11-06.md)). For any other model, or if recall misses past incidents you expect, set `EMBEDDINGS_MIN_SCORE`: past incidents scoring under it are never recalled, and unset it is 0.5 for a model Bleep has not measured. Embedding calls are not in the usage ledger: the dev panel's cost and the daily cap count only the chat models, so a paid embeddings endpoint is billed outside them. Details: [Memory](design/memory.md), [Embeddings](decisions/embeddings.md).

**For German or mixed-language docs**, `google/gemini-embedding-2` through OpenRouter is the recommended option (the right chunk first for 22 of 22 queries over sloppy German service docs where bge-small managed 18, and one English first hit lost out of 20; [German docs](evals/embeddings-german-docs-2026-11-10.md), #377). Set these four in `.env`, with your own OpenRouter key (or set the key on the dashboard); recall's cut-off is then 0.65 on its own:

```bash
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_BASE_URL=https://openrouter.ai/api/v1
EMBEDDINGS_MODEL=google/gemini-embedding-2
EMBEDDINGS_API_KEY=                                        # your OpenRouter key
```

Each embed is then a network call of about 420 ms, and a first ingest of the Docker docs and a repo's docs is about 85,000 tokens, about $0.017 at OpenRouter's $0.20 per million input tokens (its models API, read 2026-11-10). Installs without a key keep the local bge-small. If OpenRouter is unreachable (read from the code, not run live), each embed fails after up to 30 s: Gather's recall is left out as a gap, `knowledge_search` and `recall_incidents` answer the model that the tool failed and the run goes on, saving the finished incident to memory is logged and lost while the report is still sent, and knowledge ingest tries again in the background.

## Tracing (optional)

Every incident run can become one trace, with the incident as its session. For a self-hosted Langfuse, fill in the `LANGFUSE_INIT_*`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` and `LANGFUSE_NEXTAUTH_SECRET` lines of `.env` (`.env.example` says how), then:

```bash
docker compose -f docker-compose.yaml -f docker-compose.observability.yaml --profile observability up -d --build
```

Langfuse creates its project, keys and login from `.env` on first start; the UI is at http://localhost:3001, and each incident carries a link to its trace (`trace_url`). The links use `LANGFUSE_UI_URL` from `.env` (default `http://localhost:3001`); set it when the browser reaches Langfuse at another address. An unreachable Langfuse does not hold up incidents; the backend logs a warning for each batch of spans it could not send. For Langfuse elsewhere or LangSmith, set `TRACING` and the variables listed in `.env.example` instead; the keys (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGSMITH_API_KEY`) can be set on the dashboard first, then `TRACING` in `.env` and a restart. Pass `docker-compose.observability.yaml` only with the profile: it turns the backend's tracing on, and without the profile no Langfuse runs. Bleep refuses to start when `TRACING` names a tracer whose keys are missing. Details: [Observability](design/observability.md).

## CI

`.github/workflows/ci.yml` proves the repo builds and its checks pass on a clean `ubuntu-latest` machine. The repo is private, so it does not run on push or pull request: open the **Actions** tab, pick **CI** and choose **Run workflow** (or `gh workflow run ci.yml`). It needs no secrets and no `.env`.

| Job | Runs |
|---|---|
| backend | `uv sync --all-groups`, `ruff check`, `ruff format --check`, `pytest -q`, and `pytest tests/evals` with the `evals` group so the GEval judge tests run instead of skipping |
| docs | `scripts/check_docs.py` |
| frontend | `npm ci`, `format:check`, `lint`, `typecheck`, `test`, `build` (Node 24, as in the image) |
| compose | `docker compose build` and `docker compose --profile demo build`; `.env` is optional in the Compose file, so no placeholder is needed |

Tests use fakes, so nothing touches the network or a real model. Real-model evals are not part of CI.

## Not built

- **Always-on on a small machine that stays up** (a spare Mac or a mini PC), so Bleep watches its hosts while your laptop sleeps.
- **A public deployment behind a reverse proxy, with hardened auth in front of the dashboard.** Until that exists, never expose port 3000 to the internet.
