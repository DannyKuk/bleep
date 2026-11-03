# Setup

## Quick start (demo)

The demo profile lets Bleep watch the Docker engine of the machine it runs on, through a filtered socket proxy ([why](decisions/socket-host.md)).

```bash
cp .env.example .env    # set BLEEP_ADMIN_PASSWORD (12+ characters) and BLEEP_SECRET (a long random string)
docker compose --profile demo up -d --build
```

Open http://localhost:3000 and log in with the admin password. Bleep refuses to start with a shorter password. A random secret: `openssl rand -hex 32`.

For something to watch, start the demo shop with `docker compose -f demo/compose.yaml up -d --build`. It serves on port 8080; [Demo](demo.md) lists its faults and how to switch them.

Without the `demo` profile only the backend and the dashboard start, and the hosts file has to name a host you reach some other way.

The data (the SQLite database) lives in the `bleep-data` volume. `docker compose down` keeps it; `docker compose down -v` deletes it.

### A first incident, end to end

This runs the whole path from a dead container to the approval question, on the dashboard and in Telegram. It needs a model key and, for the Telegram half, a bot. Tests never do this; it is the manual check.

1. In `.env`, besides the two `BLEEP_` values: `OPENROUTER_API_KEY` (or `LLM_PROVIDER=local` with `LOCAL_BASE_URL` and `LOCAL_MODEL`), and for Telegram `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (send the bot a message first, then read the chat id from `https://api.telegram.org/bot<token>/getUpdates`).
2. Start Bleep and a throwaway container for it to watch, with no restart policy:
   ```bash
   docker compose --profile demo up -d --build
   docker run -d --name bleep-try --restart no nginx:1.27-alpine
   ```
   Log in at http://localhost:3000. Within about 30 s the overview lists `bleep-try` under the Demo stack as running.
3. Break it: `docker kill bleep-try`. Use `kill`, not `stop`: a `docker stop` (exit 0 or 143, a `stop` event) is someone's choice and opens nothing, by design ([watcher](design/watcher.md)). A kill leaves exit 137, the same as a crash, and the restart policy `no` keeps it down.
4. What to expect:
   - **Overview:** the service turns red (Down) at the next tick. After the 60 s grace window it is still down, so an incident opens, and the row gets an "Open incident" link. Allow about 75 s from the kill.
   - **Incident page** (the link, or Incidents in the menu): the badge at the top right says Live. The timeline fills in (Gathered context, Investigating), and Live reasoning lists the tool calls as they happen, for example `inspect_container container=bleep-try`, each with its result folded under it. Then the Diagnosis card shows the cause (killed, exit 137, restart policy `no`), and the Proposal card a plan such as `docker.start bleep-try on demo` with Approve, Reject and "Tell Bleep something". The status badge says Awaiting approval.
   - **Telegram:** the same plan arrives as a message with Approve and Reject buttons; a reply to it is an instruction.
5. Answer on one side, then try the other. The dashboard locks its buttons and says, for example, "Approved on the dashboard"; a tap in Telegram afterwards is told the dashboard already decided. Approve in Telegram first instead, and the page shows "Approved on Telegram" with its buttons locked; a page that has not caught up yet answers a click with "Already decided on Telegram".
6. Clean up: `docker rm -f bleep-try`.

### Approve, run, verify and report

This continues past the approval: Bleep restarts what is down, checks the service and writes the report. It needs the same model key (and the bot for the Telegram half). Tests never do this; it is the manual check for Execute, Verify and Report.

1. With Bleep running as above, start a small stack whose web container depends on a database, both with no restart policy and one Compose project label, so the overview groups them as one service:
   ```bash
   docker run -d --name bleep-try-db --restart no --label com.docker.compose.project=bleep-try redis:7-alpine
   docker run -d --name bleep-try-web --restart no --label com.docker.compose.project=bleep-try nginx:1.27-alpine
   ```
   Within about 30 s the overview lists the `bleep-try` service with both containers running.
2. Stop the dependency the way a crash does: `docker kill bleep-try-db` (a `docker stop` opens nothing, by design). After about 75 s an incident opens, and the Proposal card shows a plan such as `docker.start bleep-try-db on demo`.
3. Approve on the dashboard. What to expect:
   - **Timeline:** "Approved on the dashboard", "Ran the plan" with `docker.start bleep-try-db: started` and `docker.start bleep-try-db: done` under it, then "Checking the service" for about 20 s and "Check 1 passed" (a failed check is followed by up to two more, 10 s apart).
   - **Report card:** the outcome "Fixed and verified", the action taken `docker.start bleep-try-db on demo: done`, "Approved on the dashboard" and the timeline. The incident's status is Resolved.
   - **Telegram:** the verification result and then the report arrive in the chat.
   - `docker ps` shows `bleep-try-db` running again, with a new start time.
4. Optional, the stale-plan refusal: kill the database again, wait for the next proposal, and before approving run `docker start bleep-try-db` yourself. Approve: the timeline says "The plan did not run, the state changed since approval: bleep-try-db on demo was started again. Planning again.", nothing is restarted, and a new plan is proposed for the container as it is now (usually no action, since it is running).
5. Clean up: `docker rm -f bleep-try-db bleep-try-web`.

If the page stays on Connecting, or the reasoning only appears when the run ends, the stream is being buffered between the browser and the dashboard ([why it can happen](decisions/sse-through-next.md)).

## Real hosts

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

- **Host keys are always verified, and it fails closed.** Bleep checks the `known_hosts_file` of the host if set, else `~/.ssh/known_hosts` inside the container. With neither, or with a key that doesn't match, it does not connect. There is no setting that turns checking off ([design](design/hosts.md)).
- **Encrypted private keys are not supported**, since there is no ssh-agent in the container. Use a dedicated, unencrypted deploy key for Bleep, give it to Bleep only, and revoke it by removing it from `authorized_keys`.
- **The SSH user needs access to the Docker socket, which is root-equivalent.** On Linux that means the user is in the `docker` group, or is root. Anyone who can use that key can take over the host. Treat the key like a root password: a dedicated key, a dashboard on a trusted network only, and nothing else sharing the secrets directory.
- **Docker Desktop on macOS** keeps its socket in the user's home, so the host needs `docker_socket: /Users/<user>/.docker/run/docker.sock`. The remote login also has to be on (System Settings, Sharing, Remote Login). How the socket is forwarded: [Docker over SSH](decisions/docker-over-ssh.md).

## Network and cookies

The dashboard (port 3000) is for a trusted LAN only. It has one admin login and no rate-limited public hardening. The backend's port 8000 is published on `127.0.0.1` only, so other machines reach Bleep through the dashboard, which forwards `/api` to the backend.

The watcher checks every host every 15 s; `BLEEP_WATCH_INTERVAL_S` in `.env` changes that.

The session cookie is not `Secure` by default, because the dashboard is plain http on the LAN. If you put it behind https, set `BLEEP_COOKIE_SECURE=true` in `.env`.

## Telegram (optional)

Without it everything works from the dashboard. To also get incidents and approve from your phone:

1. Create a bot with BotFather and put its token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Send the bot any message, then read your chat id from `https://api.telegram.org/bot<token>/getUpdates` (`result[0].message.chat.id`) and set `TELEGRAM_CHAT_ID` in `.env`.
3. Restart Bleep. A proposal arrives with Approve and Reject buttons; reply to the message to give an instruction instead.

Only that chat is answered. Messages and button taps from any other chat are ignored and recorded in the guard log. The first answer wins, from Telegram or the dashboard; the other is told who won. Details: [Notifications](design/notify.md).

## Memory embeddings (optional)

Bleep remembers every finished incident and recalls similar ones. By default it embeds them with a small local model that ships inside the backend image (bge-small, about 11 ms per incident on a laptop, no key and no network). To use an OpenAI-compatible endpoint instead (OpenAI, LM Studio, Ollama), set in `.env`:

```bash
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_BASE_URL=http://host.docker.internal:1234/v1   # the server's OpenAI-compatible base URL
EMBEDDINGS_MODEL=text-embedding-nomic-embed-text-v1.5     # a model that server offers
EMBEDDINGS_API_KEY=                                        # only if the server wants one
```

Bleep refuses to start with the provider set and the URL or the model missing. After a model change, the next start embeds the stored incidents again; until that is done they are left out of recall. Details: [Memory](design/memory.md), [Embeddings](decisions/embeddings.md).

## Tracing (optional)

Every incident run can become one trace, with the incident as its session. For a self-hosted Langfuse, fill in the `LANGFUSE_INIT_*`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` and `LANGFUSE_NEXTAUTH_SECRET` lines of `.env` (`.env.example` says how), then:

```bash
docker compose -f docker-compose.yaml -f docker-compose.observability.yaml --profile observability up -d --build
```

Langfuse creates its project, keys and login from `.env` on first start; the UI is at http://localhost:3001, and each incident carries a link to its trace (`trace_url`). For Langfuse elsewhere or LangSmith, set `TRACING` and the variables listed in `.env.example` instead. Bleep refuses to start when `TRACING` names a tracer whose keys are missing. Details: [Observability](design/observability.md).

## Later

- Always-on on the spare MacBook, so Bleep watches the hosts when this laptop is closed.
- A public deployment behind the Coolify proxy, with hardened auth in front of the dashboard. Until that exists, do not expose port 3000 to the internet.
