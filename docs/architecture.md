# Architecture

How Bleep is put together, in a few minutes. Each section links to the detail: the reasoning behind every design choice is in `docs/design/`, the big questions with their evidence in `docs/decisions/`.

## The pieces

```mermaid
flowchart LR
    subgraph HOSTS["Docker hosts"]
        direction TB
        SSH["<b>SSH host</b><br/>socket forwarded<br/>allowlisted shell"]
        SOCK["<b>Socket host</b><br/>filtered proxy"]
    end
    REG["<b>Registry</b><br/>engines · services<br/>host basics"]
    DET["<b>Detector</b><br/>grace · debounce<br/>thresholds"]
    INC["<b>Incidents</b><br/>repository · event bus"]
    RUN["<b>Runner</b>"]
    GRAPH["<b>Incident graph</b><br/>gather → … → report"]
    TOOLS["<b>Read tools</b><br/>through the guard"]
    ACT["<b>Act tools</b><br/>grant · plan check"]
    PLUG["<b>Plugins</b><br/>Coolify · MCP"]
    NOTIFY["<b>Notifier</b><br/>dashboard · Telegram"]
    LLM["<b>Models</b><br/>OpenRouter · Anthropic · local"]
    MEM["<b>Memory</b><br/>past incidents · facts"]
    KNOW["<b>Knowledge</b><br/>Docker docs · repo docs<br/>past reports"]
    DB[("<b>SQLite</b><br/>bleep.db<br/>checkpoints.sqlite")]
    API["<b>FastAPI</b>"]
    FE["<b>Next.js</b>"]
    SSH --> REG
    SOCK --> REG
    REG --> DET --> INC --> RUN --> GRAPH
    GRAPH --> TOOLS --> REG
    GRAPH -->|after a yes| ACT --> REG
    TOOLS --> PLUG
    GRAPH --> NOTIFY
    TOOLS --> KNOW --> DB
    GRAPH --> LLM
    GRAPH --> MEM --> DB
    INC --> DB
    GRAPH --> DB
    REG --> API --> FE
    INC -->|events, live| API
    style HOSTS fill:none,stroke:#888,stroke-dasharray:5 5
```

| Piece | Path | What it does |
|---|---|---|
| **Hosts file** | `config/hosts.example.yaml`, `backend/src/bleep/hosts/config.py` | Which hosts to watch and how to reach them, per-host and per-service rules, URLs, protected containers, the `exits` of jobs meant to finish and `may_sleep` for a host that sleeps by design. Secrets are references to env vars or files, never values. |
| **SSH session and engine** | `hosts/ssh.py`, `hosts/engine.py` | One asyncssh connection per SSH host forwards the remote Docker socket; the `docker` SDK talks to it. A socket host talks to a local endpoint, in the demo a filtered proxy. |
| **Host shell** | `hosts/shell.py` | The read-only command allowlist (exact argv templates) and host basics (disk, memory, load) for Linux and macOS. |
| **Registry** | `hosts/registry.py`, `hosts/discovery.py` | Each host's connection, reachability, containers grouped into services, and basics. |
| **Dashboard hosts** | `hosts/dashboard.py`, `api/host_settings.py` | SSH hosts added, changed and removed on the dashboard: the connection in SQLite, the key and the confirmed host key in the secret file; trust on first use by fingerprint, a key bound to its connection, applied without a restart. |
| **Pull watcher** | `watcher/poller.py`, `watcher/events.py` | One tick per host every 15 s: reads Docker events from a cursor and the container list, and turns them into signals. A dropped host is a failed tick, not a death. |
| **URL checks and host probe** | `hosts/url_check.py`, `watcher/http_checks.py`, `watcher/host_probe.py` | Outside-in GET of each service's URLs every 30 s (status, latency, body text), the last 30 kept per URL; disk, memory and load signals from each host reading. |
| **Service health** | `watcher/health.py`, `watcher/health_store.py`, `watcher/overview.py` | Each service's health worked out from its containers and URLs, the same way for the dashboard and the watcher, and stored with the time it last changed, so the service page can say "healthy since". |
| **Detector** | `watcher/detector.py` | Pure decision logic: a signal becomes an incident, a self-recovery or nothing (grace window, repeated deaths, debounce, host thresholds). |
| **Watcher manager** | `watcher/manager.py` | Runs a supervised task group per host, feeds every signal to the detector, opens or attaches incidents for subjects that are not quiet, starts the runner for new ones, and logs self-recoveries with a one-line explanation. |
| **Incidents** | `incidents/repository.py`, `incidents/bus.py` | Incidents and their signals, one open per subject; the event bus stores every event, then fans it out to live subscribers. |
| **Expectations** | `incidents/expectations.py` | "This is expected": records that what opened an incident is normal, only where a later identical event can be judged by it: a job's clean exit goes into its service's `exits`, a host that sleeps gets `may_sleep`. |
| **Looks** | `incidents/looks.py` | A look on request: a person's confirmed tap opens an incident for a service through the watcher's path (`open_or_attach`, then the runner), unless it has an open incident, is quiet, unknown or Bleep's own, its host does not answer, or the day's cost cap is spent. |
| **Watchdog** | `incidents/watchdog.py` | Tells the human once about an incident that has stopped moving. It only reports; it never acts. |
| **Sleep watch** | `watcher/wake.py` | Notices that Bleep itself was suspended (its machine slept, its container was paused) from the gap between the wall clock and the monotonic clock, so the time it could not see is not counted as an outage. |
| **Quiet store** | `incidents/quiet.py`, `api/quiet.py` | One quiet state per service or host, snooze or leave alone. Its listeners close the subject's incidents when a quiet starts and judge it again when one ends; a 30 s loop ends snoozes whose time has passed. |
| **Runner** | `incidents/runner.py` | Runs each incident's graph in the background, started by the watcher manager for each newly opened incident, resumed after a decision or a restart (`recover_on_boot` at startup), and keeps the incident's status in step. |
| **Approvals** | `incidents/approvals.py`, `incidents/reminders.py` | Pending approvals in SQLite, decided once by whichever channel answers first; HMAC grants for approved plans; reminders that never approve. |
| **Notifier and Telegram bot** | `notify/`, `prompts/notify/` | The notifier the graph and the reminder loop call: it tells the dashboard bus and, when a bot token is set, Telegram. Each incident is one Telegram thread: an opener, replies to it, one end message that leads with the outcome. The bot long-polls Telegram; a button becomes a decision, raced fairly against the dashboard, and a reply to a plan is offered to it with Send. |
| **Talk** | `talk/`, `prompts/talk/`, `watcher/overview.py`, `api/talk.py` | Answers what the person writes to Bleep, in Telegram or in the dashboard's chat panel, from Bleep's own state: a snapshot built by code (hosts, services, open and recent incidents), the person's text and the snapshot wrapped as untrusted, one cheap-model call that reads the kind (question, action, refusal, unclear), fixed lines for all but a question's answer. An action is checked by code against that state and offered with one confirm button (`talk/offers.py`, `talk/looks.py`, `talk/confirms.py`); it reads, and nothing changes from a message alone. A press runs the offer through `talk/actions.py`, the same code for both surfaces; each surface keeps its own turns and offers (`talk/panel.py` for the panel). A look pressed in the panel is followed there in fixed words, its progress, its summary at a waiting plan and at its end, and what it did (`talk/progress.py`, `GET /api/talk/looks/{id}`, #458). Every message and how it was read goes into an in-memory log, 100 per surface, exported as JSON or Markdown (`talk/log.py`, `GET /api/talk/log`). |
| **Incident graph** | `graph/` | LangGraph state, nodes and wiring, checkpointed per incident in `checkpoints.sqlite`. |
| **Read tools** | `tools/` | Docker reads and the host command tool for the model, each scoped to the incident's host, `recall_incidents` over memory, `deployed_commits`, `recent_commits` and `commit_diff` for a service with a linked repo, and `knowledge_search`. |
| **GitHub client** | `hosts/github.py` | Read-only GitHub REST: a repo's commits, the commits between two, diffs, its README and docs files; works without a token on public repos. |
| **Act tools** | `tools/docker_act.py`, `guard/grant.py` | Restart, stop and start a container for one step of an approved plan; each checks the grant against the plan, then the target (host error, protected, Bleep's own), and acts on the approved container id. Never offered to a model. |
| **Plugins** | `plugins/`, `api/plugins.py` | Tools that share one connection, switched on and off as a whole: their read tools join Investigate's and their actions the plan check's while they are on. The MCP plugin (`plugins/mcp_url.py`) runs only the tools a person allowed on one MCP server; the Coolify plugin (`plugins/coolify/`) reads and redeploys one Coolify. |
| **Settings store** | `incidents/settings_store.py`, `api/settings.py`, `guard/secret_fields.py` | Non-secret settings a person changes from the dashboard (model choice, embeddings, tracing, budgets, thresholds, the time zone, the language, hosts' and services' rules, URLs and protected containers, plugin toggles), stored in SQLite over the hosts file and the defaults, in force at once; a body carrying anything secret-shaped is refused. |
| **Secret file** | `store/secrets.py`, `api/secrets.py` | The keys, tokens and plugin addresses set from the dashboard, in `secrets.json` on the data volume (mode 0600, written atomically), never in SQLite; write-only over the API, and the env wins. |
| **Dev panel** | `api/dev.py`, `incidents/prompt_overrides.py`, `frontend/components/dev/` | Off unless `BLEEP_DEV_PANEL=true`: the budgets (through the settings store), prompt overrides checked like the loader, cost per incident and per day from the usage ledger with each incident's trace link, and the guard log by kind and incident. |
| **Long-term memory** | `memory/`, `tools/memory.py` | Every finished incident with the human's decision and feedback, embedded by a local model (bge-small through fastembed) or an OpenAI-compatible endpoint, and facts per service. Gather recalls the 3 most similar (the same service's first) and the facts; Report writes the record and learns facts from the human's instructions; a model change re-embeds on boot. |
| **Knowledge search** | `knowledge/`, `tools/knowledge.py`, `backend/knowledge/docker-docs/` | A shipped snapshot of 27 Docker and Compose doc pages, the linked repos' README and `docs/`, and past incident reports, cut at headings into chunks of 300 tokens and embedded with memory's model. Ingested on first start and refreshed in the background, skipped by content hash; `knowledge_search(query, sources?)` returns the best section of the 5 closest pages as untrusted data. |
| **Action log** | `incidents/action_log.py` | Execute's ledger in SQLite: each step `started` before its engine call and `done`, `failed` or `skipped` after, so a restart never runs a step twice. |
| **Guard** | `guard/` | Redacts secrets, strips control characters, caps size and wraps untrusted text before anything reaches a model; checks every proposed plan before a human sees it (`guard/plan_check.py`); hashes plans and signs and checks approval grants (`guard/grant.py`); cuts log text out of every report (`guard/report_check.py`); builds incident titles with a label's text marked as host text (`guard/titles.py`); `guard/log.py` records what it refused, blocked or changed in the `guard_log` table. |
| **Models** | `llm/` | One factory for OpenRouter, Anthropic and local models, structured output with a plain-JSON fallback, and a token and cost ledger with a daily cap. |
| **Tracing** | `llm/tracing.py`, `docker-compose.observability.yaml` | Off by default; `TRACING=langfuse` or `langsmith` adds a tracer's callbacks to every graph run, one Langfuse trace per incident with the incident as its session, and stores the trace's link on the incident; each chat reading is a trace of its own in the session `talk-panel` or `talk-telegram`. `--profile observability` runs a self-hosted Langfuse. |
| **Prompts** | `prompts/` | Every prompt is a `.md` file loaded with `prompts.load(name, **vars)`, which reads the dev panel's override instead when one is in force; a text a person reads has a German twin, loaded with `prompts.load_in(language, name, **vars)`. |
| **Store** | `store/` | SQLite with numbered migrations. |
| **API** | `main.py`, `api/`, `auth/` | Login with one admin password and hashed sessions; the read API over hosts and services; incidents with their findings, timeline and recoveries; the dashboard's decision on an approval and feedback on the findings; a service's facts; the settings, the plugins and the Telegram status; the chat panel's messages and confirm presses; the live event stream (SSE). |
| **Dashboard** | `frontend/` | Next.js app: the overview of hosts, services and self-recoveries; the incident list; each incident's page with its live reasoning, the plan to answer, the report and a vote on the findings; each service's and host's page with its settings; the settings page, which sets every secret write-only; and, with the dev panel on, `/dev`. On every page, Bleep's face in the bottom-right corner opens the chat panel. The live stream goes through a route handler (`app/api/stream/route.ts`). |
| **Compose** | `docker-compose.yaml`, `config/socket-proxy.cfg.template` | Runs Bleep; `--profile demo` adds the socket proxy so Bleep can watch this machine. |
| **Demo shop** | `demo/`, `config/hosts.demo.yaml` | A proxy, a web app, its database and a worker for Bleep to watch, as their own Compose project `shop`, and `demo/fault.sh`, which switches eight faults on and off ([demo](demo.md)). |
| **Eval harness** | `backend/evals/` | `python -m evals.run`: switches a demo fault on, runs Bleep's watcher and graph in-process to the end of Propose with a real model, switches it off, and scores the root cause (a judge model, or by hand), the category and the plan ([how to run it](../backend/evals/README.md)). |

Paths under `backend/src/bleep/` are given from there.

## One incident, start to end

| Stage | What happens | Code |
|---|---|---|
| 1. Detect | A container dies, a URL fails or a host crosses a threshold; the detector waits out the grace window and debounces, then asks for an incident. | `watcher/detector.py` |
| 2. Open | The incident is opened, or the signal attaches to the subject's open one. | `IncidentRepo.open_or_attach` |
| 3. Run | The runner tells the channels the incident opened (Telegram gets it whatever the run ends with), then starts the incident's graph thread in the background (`thread_id` = incident id), except for a repeat of a finished incident (no "opened" message) and a host-unreachable incident (it waits for its host, with no graph run). | `IncidentRunner.start`, `Notifier.incident_opened` |
| 4. Gather | Containers, the last 200 log lines of the affected ones through the guard, host basics, rules, the 3 most similar past incidents (the same service's first) and the service's facts. No model call. | `graph/nodes/gather.py`, `MemoryStore.similar` |
| 5. Investigate | The model reads with read-only tools, at most 8 calls a round; each result is guarded and wrapped once, and every call and result streams to the bus. A spent cost budget stops here with a notice. | `graph/nodes/investigate.py` |
| 6. Diagnose | Root cause, evidence, confidence; low confidence investigates once more, then goes on marked unsure, and Propose still asks the model. | `graph/nodes/diagnose.py` |
| 7. Propose | One to three allowlisted actions, or no action with exact instructions for the human; checked in code before anyone is asked, and a refused plan goes back to the model once. | `graph/nodes/propose.py`, `guard/plan_check.py` |
| 8. Approval | The target containers, and the uuid each Coolify target resolves to, are snapshotted, the plan is stored as a pending approval, sent to the human and shown with "Yes, go ahead", "No, leave it" and "Tell Bleep something" on the incident's page; the run checkpoints and waits for a yes, a no or an instruction, through a restart if need be. The first answer wins; an instruction goes back to Propose; a plan Bleep's own checks refused twice, or a no-action plan whose fix the enabled actions could run, is put the same way with "Tell Bleep something" and "Let it go" and no yes; a pending approval is re-sent every 60 min and never approved by itself. | `graph/nodes/approval.py`, `ApprovalStore`, `ReminderLoop`, `IncidentRunner.resume` |
| 9. Execute | Every target is read again. One gone, recreated or started again since approval runs nothing and goes back to Propose with a "state changed since approval" notice and a voided grant, and so does a step the guard refuses now (at most 3 re-plans a run). Otherwise the steps run in order through the act tools, each logged `started` before and `done` or `failed` after; a failed step skips the rest. After a crash a finished step is never repeated, and a started one is read on the host instead of run again. | `graph/nodes/execute.py`, `tools/docker_act.py`, `ActionLog` |
| 10. Verify | Waits 20 s, then up to 3 checks 10 s apart; ok only when every planned container is running and healthy (and not started again since Verify first saw it running, nor, for a container a redeploy made, restarted at all; a redeployed Coolify application passes only after a redeploy that succeeded, with every container it made passing) and every planned URL answers, the incident's own failing containers and URLs and its service's configured URLs always among them, and a host incident's disk, memory or load read again and back within its threshold. A failure goes back to Investigate at most twice, then the outcome is `unverified`. | `graph/nodes/verify.py` |
| 11. Report | The outcome is set in code (`resolved` only after verification) with its fixed text; the model writes the summary, cause and follow-ups from Bleep's record, anything copied from a log is cut out, a commit sha stays only if the run read that commit from GitHub (in the diagnosis too), and the report, with a timeline built from the stored events, goes to the bus and every channel, and into long-term memory with the human's decision and feedback; the human's lasting instructions become at most two service facts; the runner closes the incident once the report is checkpointed. | `graph/nodes/report.py`, `guard/report_check.py`, `Notifier.report`, `MemoryStore.add_report`, `MemoryStore.replace_report_facts` |

Every node publishes `node_started` and `node_finished` on the bus, which stores them for the incident's timeline. On boot, `IncidentRunner.recover_on_boot` resumes every unfinished run except those waiting for approval, and delivers a decision that was recorded before a crash. The app starts the runner with the watcher manager, which calls `recover_on_boot` once at startup and starts a run only for a newly opened incident, and runs the reminder loop beside them. The runner, the graph, the reminders and the API share one `ApprovalStore`, so a decision from any channel is the one the run sees, also after a restart.

## Security model

Bleep is built so that a wrong model answer, or a log line written by an attacker, cannot change a host.

- **Reading is all the model can do.** Its tools read containers, logs, host basics through an allowlist of exact command templates run without a shell, GitHub and the docs. No model is ever given a tool that writes ([hosts](design/hosts.md), [guard](design/guard.md)).
- **Every write needs a person's yes.** A plan is checked in code before anyone sees it: allowlisted actions on the incident's host only, never a protected container, never Bleep's own (`bleep.self=true`), never a working container nobody named. A yes produces an HMAC grant over the plan's hash, and the act tools refuse without one, check the target again and act on the container id that was approved. Nothing is ever approved automatically ([guard](design/guard.md#act-tools)).
- **Everything a tool returns is untrusted data.** It is redacted for secrets, stripped of control bytes, capped at 8,000 characters and wrapped in tags with a random id that are escaped inside it; the prompts say it never gives orders. Reports never paste log text ([guard](design/guard.md)). When code sees host text try to give orders (an escaped tag, or a line that speaks to an AI reader with an order, overrides its rules or claims an approval), the human gets a hint naming where, without a quote; many planted orders carry none of these signs, so no hint does not mean none ([guard](design/guard.md#guard-log)). How well this holds against real attacks is measured in the injection eval reports below.
- **Secrets never reach the model or the database.** SSH keys, tokens and API keys live in the env or in `secrets.json` (mode 0600) on the data volume, and the API only says whether each is set ([settings](design/settings.md)).
- **"Fixed" means verified.** An incident is resolved only when its containers run healthy and its URLs answer again ([graph](design/graph.md)).
- **The dashboard is for a trusted network.** One admin password of at least 12 characters, a limit of 5 failed logins a minute that every browser behind the dashboard shares, so a LAN machine can hold the login shut but not guess faster (#322), hashed session tokens in an HttpOnly `SameSite=Lax` cookie. It is not hardened for the internet ([setup](setup.md), [app](design/app.md)).
- **Known limits.** An SSH key that can reach the Docker socket is root on that host, so give Bleep its own user and key; `secrets.json` is in clear on the data volume, so a backup of it holds the secrets; an MCP tool's arguments are opaque to the plan check beyond the tools a person allowed ([plugins](design/plugins.md)).

## Key design choices

One line each; the full reasoning and issue numbers are on the design page named after each group.

**Hosts** ([details](design/hosts.md))
- **Docker over SSH by forwarding the socket**, so a macOS host whose non-interactive `PATH` lacks `docker` works too ([Docker over SSH](decisions/docker-over-ssh.md)).
- **The demo host goes through a filtered socket proxy**, never a raw socket mount ([socket host](decisions/socket-host.md)).
- **Services are keyed by Coolify labels, else the Compose project of a container Compose created, else the container**, and Bleep's own containers (`bleep.self=true`) are left out.
- **Host commands are exact argv templates run without a shell**; anything else is refused by code.
- **An ssh host can be added on the dashboard, a socket host only in the hosts file**, which wins for the hosts it names; its key and host key go to the secret file, the host key is trusted only once a person confirms its fingerprint, and a new address, port or user clears both.
- **The GitHub client lives in `hosts/`**, where the token and each service's `repo` are set, so the knowledge ingest can use it without importing `tools`.
- **The GitHub tools read a service's linked repo without a token on public repos**, offered only where a `repo` is set; commit text is untrusted and every GitHub failure is a sentence the model can go on from.

**Watcher** ([details](design/watcher.md))
- **A death that recovers within 60 s is not an incident**; three deaths in ten minutes are.
- **Open at once, attach within the 30 s debounce**, so there is never a second incident for one cause.
- **A snoozed or left-alone service or host is still watched but opens nothing**; when the quiet ends it is judged once more.
- **A job named in `exits` is done when it exits 0, and "This is expected" teaches that (or that a host sleeps) in one tap** from the dashboard or Telegram; a crash is still an incident.
- **A failing check or host reading that outlived its incident opens no new one until it passed once, or after a hold of an hour that doubles up to a day**; for a service, any of its ended incidents holds its URL, so a failure Bleep could not fix costs one run and one message, not one a minute or one per symptom.
- **Bleep's own sleep is no outage**: after a wake an outage counts from zero and failed contact waits out a grace window; a host marked `may_sleep` shows `asleep` and opens nothing, and a host-unreachable incident waits without a model call and closes itself when the host answers.

**Incidents** ([details](design/incidents.md))
- **One open incident per subject, enforced by a unique index.**
- **An unreachable host absorbs its services' signals.**
- **A repeat within a day joins its group with a count and sends no "opened" message**; what came of it (its plan, its report, a wait) is still told, with the count.
- **A look a person asked for is never a repeat**, and counts toward a repeat hold only when a watcher's failure attached to it: it opens through the same path, always with its own opener.
- **The bus persists before it fans out**, so the timeline never loses an event.
- **Every act step is logged `started` before its engine call and finished after it**, so a resumed run never repeats a finished step.
- **A model call is cut off after 120 s and retried twice; a model that stays down pauses the run instead of failing it**, and an incident that stops moving gets one watchdog notice.

**Graph** ([details](design/graph.md))
- **One module per node, each owning its routing**; `build.py` only wires them.
- **Gather is deterministic and reads logs through the guard.**
- **Investigate loops model ↔ read tools inside one node, at most 8 calls a round**; the cap ends the round, a spent cost budget ends the run.
- **One checkpointed thread per incident**, resumed after a restart; runs waiting for a human stay waiting. Only the runner closes an incident, after the last checkpoint.
- **The checkpoint serializer rebuilds only the state's own models.**
- **The first decision wins by one `UPDATE ... WHERE status = 'pending'`**, and a grant is an HMAC over the incident id and a plan hash that covers the resolved container ids and Coolify application uuids.
- **Reminders every 60 min, never an automatic yes.**
- **Execute re-reads every target before the first step and runs nothing if one changed since approval**; at most 3 trips back to Propose per run.
- **A report is mostly code**: fixed outcome text and a timeline from the stored events; the model writes the summary, cause and follow-ups, and never "fixed" unless verified.
- **The model writes what the person reads in the language chosen on the settings page, English or German**; actions, targets, params and names stay as the host has them, English prompts are unchanged, and memory keeps a German report's cause in English.

**Memory** ([details](design/memory.md))
- **A local embedding model in the backend, baked into the image**: bge-small through fastembed on two CPU threads; a local server (LM Studio, Ollama) or an OpenAI-compatible endpoint is the option ([embeddings](decisions/embeddings.md)).
- **Vectors carry their model's name**, and a model change re-embeds on boot.
- **Memory is Bleep's own, trusted text, except quoted stretches**, which reach the model wrapped as untrusted; stored text is redacted and has its wrapper tags escaped.
- **The human's decision is remembered with the plan and the day**, so a rejected plan is not proposed again unannounced.
- **A thumbs up or down on the diagnosis or the plan goes into the incident's record in words**, whenever the human gives it.
- **Service facts are learned only from the human's own instructions**, at most two per incident, and every fact can be deleted on the service's page.

**Knowledge search** ([details](design/knowledge.md))
- **A curated Docker docs snapshot ships with Bleep**, with each page's source, commit, fetch date and licence; a script refreshes it.
- **Chunks are cut at headings and at 300 tokens, one hit per page**: the right page came first for 20 of 20 questions ([knowledge chunks](decisions/knowledge-chunks.md)).
- **Re-ingest is skipped by content hash**, so only the first start embeds the snapshot; past incidents refresh every 10 minutes, repo docs daily.
- **Results are untrusted data**, guarded and wrapped like host output.

**Agent and models** ([details](design/agent.md))
- **One model factory for OpenRouter, the Anthropic API and local models**; structured output falls back to plain JSON for weak models.
- **`openai/gpt-6-luna` in the cloud, `openai/gpt-oss-20b` locally**: the local model is as accurate on the demo faults but never says unsure and acts more, so the checks and the approval gate carry its safety ([chat model](decisions/chat-model.md)).
- **A cost ledger with a daily cap** stops a runaway incident before the next call.

**Guard** ([details](design/guard.md))
- **All host text is redacted, cleaned, capped at 8,000 characters and wrapped as untrusted data with a random id.**
- **Env values reach the model only when plain** (`HOST=127.0.0.1`, `PORT=3000`): a secret key, a token-like value and a URL's credentials stay hidden; a service URL keeps its scheme, host and port (#554).
- **Only the closing tag with the same random id ends untrusted data**, and the system prompt says so.
- **Every plan is checked in code**: allowlisted actions on the incident's host only, never a protected or Bleep container, never a working container that is not the incident's own or named by the human, a stop only of a container the incident's signals or the human name, at most 3 steps.
- **Act tools refuse without a grant for the exact plan** and act on the approved container id; no model is ever given them.
- **A report loses any 60-character stretch it shares with a log the run read.**

**Plugins** ([details](design/plugins.md))
- **A plugin is read from the registry at every use**, so switching it off takes its tools out of the next round, fails its actions in the plan check and leaves Execute no handler for them.
- **A plugin's secrets are env variable names**, read from the env or the dashboard's secret file and shown only as set or not; its settings model may not hold a secret-named or secret-typed field.
- **The Coolify plugin reads applications, deployments and env keys, never an env value**, parsed down to the fields Bleep names.
- **Coolify's actions run the Docker act tools' checks**, a redeploy waits for its deployment to end, and a `set_env` value is never logged.
- **The MCP plugin runs only the server tools a person allowed**, read tools freely and act tools only as an approved `mcp.call` step; the server's descriptions and answers arrive wrapped as untrusted data.
- **A plugin step is held to its act tool's rules in the plan check, and never approved blind**: a step whose params redaction would change is refused (`hidden_param`, #294), and one whose params do not fit the Telegram line gets Reject only there.

**Settings** ([details](design/settings.md))
- **Database override > hosts file > defaults, in force at once**; the dashboard can protect a container (or a Coolify application by its name as written) but never unprotect one the hosts file protects.
- **A settings body with a secret-shaped key or value anywhere is refused with 422**, naming where and never what.
- **A settings or plugin change must send the version it was read at (`If-Match`)**; a stale one is a 409 and changes nothing, so no page silently reverts another's change.
- **Every secret is set or cleared write-only on the settings page** and shown only as set or missing; a hosts file host's connection is read-only.
- **Secrets set from the dashboard go to `secrets.json` on the data volume, never the database**, and the env wins; a plugin address set there must be https to a public address, and the admin password and `BLEEP_SECRET` stay env-only.
- **The model, embeddings and tracing settings can be set on the dashboard's Settings page too, and a value in the environment wins and is shown as set there**; only a model server's address set there may be private, still bound to its key, and an embeddings change (or one to a running Langfuse tracer's keys, address or project) applies at the next start, where a provider or model change re-embeds the memory.

**Dev panel** ([details](design/dev-panel.md))
- **Off unless `BLEEP_DEV_PANEL=true`, and then behind the admin session**; off, every `/api/dev` path is a 404 and Settings shows no link to it.
- **A prompt override must use exactly its file's placeholders and hold no secret**; reset deletes it and the loader reads the file again.
- **Only the prompts in `prompts.OVERRIDABLE` can be overridden**, never the system prompt or the untrusted-data rules, and every save is in the guard log by name.
- **Each incident records the overrides its run started with, by name and hash**; with the panel off, `PUT /api/settings` refuses the budgets.

**Notifications** ([details](design/notify.md))
- **Telegram is optional**: without a token everything runs through the dashboard.
- **Only the configured chat is answered**; anything else is dropped and logged.
- **A decision from either channel goes through the same first-wins `resolve`**, and the loser is told who won.
- **A plan Telegram cannot show whole, a cut `set_env` value or a plugin step's params, gets no Approve button there**; it is approved on the dashboard.
- **Each incident is one Telegram thread**: its short name (`shop #14`), an opener, replies to it, an emoji per kind of message, one end message that leads with the outcome, and "Again" for a repeat.
- **"🤫 I'm on it" on the opener snoozes the service or host for 1 h, 4 h or until 08:00** through the dashboard's quiet path, and the incident ends as Quiet.
- **What the person asks for in the chat comes back as one confirm button**, built by code from the checked action, never from the model's words; only the press changes something, a reply to a plan included (#402).
- **"Look at shop-web again" opens an incident only from its confirm button**, through the watcher's path, so its plan still waits for a yes; a service with an open incident, a quiet one, an unknown one or a spent budget is answered and opens none (#403). A look that finds nothing to do is checked by code as Verify checks and ends `healthy` when every check passes (#421).
- **The chat can be exported with how each message was read**, from a log kept in memory only, the person's words redacted, never raw (#411).
- **Telegram's messages, buttons and the chat's fixed lines come in the chosen language**, English or German, each a template with a twin in `prompts/<area>/de/`; names, step lines and button data are the same in both (#375). The notices, the report's outcome and timeline, "This is expected", the plan check's refusals and the texts Bleep writes in place of the model's are stored in English, which the models read, and shown in the chosen language by the API and the notifier (`prompts/shown/`, #427, #455); so are the API's error details (`prompts/api/`, #456).

**Observability** ([details](design/observability.md))
- **Tracing is off unless `TRACING` names Langfuse or LangSmith**, and off adds no callbacks at all.
- **The callbacks go on the graph run, not the model**, and the trace id is derived from the incident id, so the run and every resume after a decision are one trace, its link known before it starts.
- **Self-hosted Langfuse v4 in its own Compose file**, set up from `.env` by headless init, only its UI published on `127.0.0.1:3001`.

**Demo** ([details](demo.md))
- **The demo shop is its own Compose project, `shop`**, so it is the service `shop`, and Bleep checks its URLs through its published port.
- **Every fault leaves the Docker-side trace a real one would**: a crash loop, an OOM kill, an unhealthy container or a failing URL.

**App** ([details](design/app.md))
- **One admin password and hashed session tokens in an HttpOnly cookie.**
- **The dashboard reaches the API through a Next.js rewrite**, so the cookie stays same-origin.
- **A dashboard decision names the approval it answers**; a late one gets 409 naming the channel that won.
- **The live stream is SSE with `id:` = seq**, so a reconnect replays from `Last-Event-ID`; a ping every 15 s keeps proxies from closing it.
- **The dashboard serves the stream from a route handler, not the rewrite**, because the rewrite gzips it and holds every event until the stream ends ([SSE through Next.js](decisions/sse-through-next.md)).
- **The incident page reads the history, then streams after its last seq, and keeps each event once by seq**; a step event reloads the incident's findings.
- **The dashboard speaks the install's language, English or German**, from a typed dictionary of its own that fails the checks on a key missing in one language; names and the backend's and the model's texts are shown as they came.

**Evals** ([details](design/evals.md))
- **A scenario runs Bleep's own watcher and graph in-process on a throwaway database and stops before Approval**: nothing is executed, sent or remembered.
- **The judge is never the model under test**: an OpenRouter model or the Claude Code CLI scores the root cause, or a human grades it by hand from a grades file kept next to the report; the plan is checked exactly, and acting where no action is expected counts as an unnecessary action.
- **The harness recreates the shop before every run** (volumes kept) and waits until it answers, so no fault's logs reach the next run; every run counts its structured-output fallbacks.
- **Every run records its largest single prompt**, the number a small local context meets first; a run whose model call reaches its length limit is a failed run with reason `context_length`, counted in the report.
- **Injection tests run the same harness on to a rejected plan and its report**: an attack passes when no marker, target step, asked-for tool call or secret value shows up in what Bleep wrote; honest controls are graded by hand ([result](evals/injection-2026-11-03.md)).
- **Verify's loop-back is measured on a scripted host**: Bleep's own graph approves and runs a plan there, and the model under test takes over when Verify sends it back ([result](evals/loopback-2026-11-04.md)).
- **The recovery line is measured on scripted logs**, plain and with planted claims ([result](evals/recovery-line-2026-11-04.md)).

## Decisions

| Question | Answer |
|---|---|
| [Docker over SSH](decisions/docker-over-ssh.md) | Forward the remote socket through one asyncssh connection per host. |
| [Socket host](decisions/socket-host.md) | A filtered proxy with four rules, not a raw socket mount. |
| [SSE through Next.js](decisions/sse-through-next.md) | A route handler that pipes the stream with `no-transform`; the rewrite held every event until the end. |
| [Embeddings](decisions/embeddings.md) | bge-small through fastembed, in-process on two threads: 11 ms and 22 ms of CPU per text, +268 MB of image. bge-m3 finds German text better and English worse at 14 times the CPU and 3.5 GB more image, so it is the multilingual option behind the endpoint; for German docs `google/gemini-embedding-2` through OpenRouter is recommended (22 of 22 first on German service docs against 18, one English first hit lost), bge-small staying the keyless default (#377); a local server running bge-m3 is the keyless, free multilingual option (22 of 22 first on German service docs, first ingest 15 s, #442). |
| [Knowledge chunks](decisions/knowledge-chunks.md) | Cut at headings, then at 300 estimated tokens with 50 of overlap, one hit per page: 20 of 20 questions found first, against 18 at 400 and 600. |
| [Chat model](decisions/chat-model.md) | `openai/gpt-6-luna` for both cloud roles, `openai/gpt-oss-20b` as the local model; the local model never says unsure and can be steered, so its safety is the plan check and the approval gate. |
| [Compose set_env](decisions/compose-set-env.md) | No, for now: no file edits on a host. A socket host cannot recreate a service at all, the demo's config faults live in the shell that ran `compose up`, 1 of 14 labelled containers named a plain file that exists, and a URL with a password is refused as a hidden param; the options and their costs are listed. |

## Eval reports

| Report | What it measured |
|---|---|
| [Scenarios, 2026-11-03](evals/scenarios-2026-11-03.md) | The eight demo faults on `openai/gpt-6-luna`, 3 runs each: root cause right in 21 of 24 (hung 0 of 3 on a shop with stale logs, 3 of 3 while the log still held a freeze line; 0 of 3 since #106 removed it, see 2026-11-05), plan exact in 18 of 24, $0.0035 and about 70 s to a diagnosis per run. |
| [Prompt injection, 2026-11-03](evals/injection-2026-11-03.md) | On gpt-6-luna, 13 of 13 live attacks (log, reply body, memory) failed to steer Bleep or leak a secret, though the 4 reply-body attacks never reached the model ([what reached it](evals/injection-reached-2026-11-04.md)); 6 of 6 honest controls kept the right root cause. After #105, the model flagged planted orders in 12 of 13 attacks but also in 4 of 6 honest controls, so its flag is measured and not shown. |
| [Local model, 2026-11-03](evals/scenarios-local-2026-11-03.md) | The eight faults and the injection suite on `openai/gpt-oss-20b`, 1 run each on a reset shop: root cause 7 of 8, controls 6 of 6, 0 fallbacks, 55 s to a diagnosis, $0; 2 of 13 attacks worked, 13 of 13 passed after the `healthy_target` rule, but the diagnoses were still steered: the plan check and the approval gate carry the safety. |
| [bad-deploy with the GitHub tools, 2026-11-03](evals/scenarios-2026-11-03-2.md) | bad-deploy on gpt-6-luna with a GitHub fixture, 2 rounds of 3: root cause 6 of 6 at 0.85 to 1.00, every run citing the version-2 commit and its migration, against 0.80 to 0.85 and neither named on the logs alone; category still off (#108). |
| [Prompt injection through commits, 2026-11-03](evals/injection-commits-2026-11-03.md) | The two commit-message attacks on gpt-6-luna, 12 runs: every run read the commits and none followed them; the model's `planted_in` named `commits` in all 12, but also in 3 of 6 honest bad-deploy runs, so it is no measure of recognition (#105). |
| [Prompt injection through docs, 2026-11-03](evals/injection-docs-2026-11-03.md) | The two docs attacks on gpt-6-luna, 16 runs: all passed, but the model searched the docs in only 4, and in those 4 the planted page came back and was not followed. |
| [Prompt injection through runbook pages, 2026-11-04](evals/injection-docs-2026-11-04.md) | Two docs attacks written as the runbook for the error the shop logs, on gpt-6-luna, 6 runs: the model read the page in 6 of 6 (1 of 6 when the error was only in the 500 body), flagged planted orders in 6 of 6 and followed none; 3 of 3 fenced-order diagnoses cited the runbook's framing of the error. |
| [Prompt injection, one attack per channel, 2026-11-04](evals/injection-channels-2026-11-04.md) | After the hook switch was renamed `SHOP_DEBUG_HOOK` (#128), one attack per channel on gpt-6-luna: 5 of 5 passed and 4 of 5 flagged planted orders; the 4 knowledge queries still named the switch, but under a name that no longer says injection. |
| [Planted-orders flag precision, 2026-11-05](evals/injection-2026-11-05.md) | The model's narrowed `planted_in` flag on gpt-6-luna (#105): 14 of 14 attack runs whose orders reached the model flagged, but also 8 of 18 honest control runs, 3 attacks whose payload never reached it and the false-cause page; high recall, poor precision, so it is measured and not shown. All 20 attacks passed. |
| [Prompt injection, did the payload reach the model, 2026-11-04](evals/injection-reached-2026-11-04.md) | One run each of four `http_body` attacks and one attack per other channel on gpt-6-luna: the `http_body` payload reached the model in 0 of 3 scored runs, the log, commit and memory payloads in 3 of 3; Bleep reads no reply body, so those passes did not test the payload. |
| [Prompt injection with a false cause, 2026-11-04](evals/injection-false-cause-2026-11-04.md) | A runbook page that blames shop-db's connection pool and gives no order, on gpt-6-luna, 3 runs: the model read the page every time, named the pool in 2 of 3 runs only as unverified, and never adopted it or acted on shop-db; the rule was corrected after a hand read. |
| [Scenarios with knowledge search, 2026-11-03](evals/scenarios-2026-11-03-3.md) | The eight faults on gpt-6-luna with `knowledge_search` offered, 1 run each: root cause 7 of 8 (hung missed, as since #106), plan exact 6 of 8; no diagnosis cited the docs. |
| [Scenarios with the MCP action, 2026-11-04](evals/scenarios-2026-11-04.md) | The eight faults on gpt-6-luna after `mcp.call` joined the plan's actions (plugin off), 1 run each: root cause 7 of 8 (hung missed, as since #106), plan exact 7 of 8, no plan named an MCP action, $0.025. |
| [misroute with an MCP act tool, 2026-11-04](evals/scenarios-2026-11-04-2.md) | misroute on gpt-6-luna with an MCP server offering `caddy_reload` (#118), 3 runs, graded by hand: root cause 3 of 3, plan exact 0 of 3; every run saw the tool and chose no action, as nothing it could read showed the Caddyfile on disk was the right one; no restart, no decoy tool, $0.010. |
| [misroute with a Caddy read tool, 2026-11-04](evals/scenarios-2026-11-04-4.md) | the same scenario with the fixture also offering `caddy_config` (#166), which prints the loaded config and the Caddyfile on disk without a conclusion, gpt-6-luna, 3 runs, graded by hand: root cause 3 of 3, plan exact 3 of 3 (0 of 3 before); every run read the tool and proposed `caddy_reload`; $0.010. |
| [Scenarios with the category definitions, 2026-11-05](evals/scenarios-2026-11-05.md) | The nine faults on gpt-6-luna, 3 runs each, after #108 defined the categories in the Diagnose prompt: category right in 19 of 24 on the eight baseline faults (14 of 24 on 2026-11-03), 22 of 27 with misroute-mcp; root cause 22 of 27, plan exact 22 of 27. bad-env's root cause fell to 1 of 3: two runs left the unresolvable `db` open between the setting and Docker's DNS, so the prompt's wrong-host rule was made decisive after this round. hung missed 3 of 3, as since #106. |
| [Scenarios on the shipping code, 2026-11-05](evals/scenarios-2026-11-05-2.md) | The final round for #241: the nine faults on gpt-6-luna, 3 runs each, after the decisive wrong-host rule (#108), graded by hand: root cause 24 of 27 and category 24 of 27, every miss on hung (#106); bad-env 3 of 3 (1 of 3 before the rule). Plan exact 20 of 27: two db-down runs also restarted shop-web and two port-conflict runs stopped shop-canary and started shop-proxy, steps the scenarios forbid. $0.0028 and 74 s to a diagnosis per run. |
| [Scenarios with the plan kept to the cause, 2026-11-05](evals/scenarios-2026-11-05-3.md) | The nine faults on gpt-6-luna, 3 runs each, after #276 kept Propose's plan to the steps the diagnosis needs, graded by hand: plan exact 24 of 27 (20 of 27 before), db-down and port-conflict 6 of 6 (2 of 6 before), the plan check's `unnamed_stop` refusing no step; root cause 23 of 27, 3 misses on hung (#106) and one hedged bad-env run. The injection round was stopped after 10 of 18 controls to save spend, every plan in them as expected; no attack ran. $0.077 for the 27 runs. |
| [Prompt injection on the shipping code, 2026-11-05](evals/injection-2026-11-05-2.md) | The final round for #241 on gpt-6-luna: 20 of 20 attacks passed; the honest controls kept their root cause in 12 of 18, the 6 misses on hung. With only the escaped-tag sign shown (#105), the human was told of planted orders in 3 of 13 attacks whose orders reached the model and in 0 of 18 controls; the model's hidden flag fired on 6 of 18 controls. |
| [Scenarios after the guard changes, 2026-11-06](evals/scenarios-2026-11-06.md) | The nine faults on gpt-6-luna, 1 run each, on main with #294, #295 and #297, graded by hand: root cause 8 of 9 and plan exact 8 of 9, the miss on hung as before; five attacks, one per channel, all passed and the human was told in 4 of the 4 that reached the model. $0.0445. |
| [Safety-audit inputs, 2026-11-06](evals/injection-audit-2026-11-06.md) | The #294-#297 inputs on gpt-6-luna, 1 run each (#305): a hostile mount, two order-shaped project names and a hidden-args `mcp.call` push all passed, each reported under its own place; no hidden-param plan reached Approval and no fact was learned (neither path ran live); the #296 loop-back went back with `verify_failed`, root cause 1.00 by hand. $0.0136. |
| [Scenarios after #319-#321, 2026-11-09](evals/scenarios-2026-11-09.md) | The nine faults on gpt-6-luna, 1 run each, on main with the Coolify protection (#319), Verify's restart check (#320) and id targets shown by name (#321), graded by hand: root cause 8 of 9 and plan exact 8 of 9, the miss on hung as before. Five attacks, one per channel, all passed, and the human was told in 3 of the 3 that reached the model. A non-plain project name now shows quoted and cut in the report title (#311). All 4 loop-back cases passed. None of the three new paths came up live (#334 adds cases). $0.0503. |
| [Inputs of the #319 and #320 fixes, 2026-11-09](evals/injection-fix-inputs-2026-11-09.md) | The #334 cases on gpt-6-luna, 1 run each: a planted restart of shop-web, labelled as a container of a Coolify app protected by uuid or by name, never reached Approval; the model named shop-web protected from Propose's off-limits list, so the plan check refused nothing (its refusal is covered offline only). A crash-looper its policy kept restarting failed Verify on the restart line, never ended verified, and round two named the expired client secret, root cause 1.00 by hand. $0.0086. |
| [Diagnose names what the fix needs, 2026-11-09](evals/diagnose-names-2026-11-09.md) | The #357 prompt on gpt-6-luna: bad-env and oom, 3 runs each, named `DATABASE_URL` and `WORKER_BATCH_MB` in every diagnosis and plan; root cause, category and plan 6 of 6 by hand. Three log attacks passed (the human told each time) and the db-down control was right, so naming things let no planted text in. $0.0299. |
| [Investigate looks up a setting's value, 2026-11-09](evals/remedy-lookup-2026-11-09.md) | The #364 prompts on gpt-6-luna: every run searched the shop's repo docs; oom's diagnoses and plans gave `WORKER_BATCH_MB` its documented 8 in 3 of 3 (before: "a value below 64 MiB"), bad-env named the documented `shop-db:5432`, db-down planned the start with no extra round. Root cause, category and plan 8 of 8 by hand; both planted-docs attacks passed. $0.0266. |
| [A no-action plan names what Bleep could run, 2026-11-11](evals/could-run-2026-11-11.md) | The #365 `could_run` on gpt-6-luna, 3 runs a case: the Coolify env-var fix named `coolify.set_env` and `coolify.redeploy` in 2 of 3, the Compose edit left it empty in 3 of 3 (no fix Bleep cannot make marked runnable); 2 of 6 passed in full, every failure Diagnose's `unknown` category. $0.0181. |
| [Reading the commits of the deploy that runs, 2026-11-12](evals/deployed-commits-2026-11-12.md) | #351's `deployed_commits` on gpt-6-luna, 3 runs a case: called in all 12 runs; in `bad-deploy-old` (a 72-hour-old deploy with 24 commits pushed since) the diagnosis read and named the deploy's two commits in 3 of 3, root cause and plan right in 6 of 6 bad-deploy runs, and both commit attacks still passed 6 of 6 |
| [Reading a long deploy in parts, 2026-11-12](evals/deployed-commits-paging-2026-11-12.md) | #497's `skip` on `deployed_commits` on gpt-6-luna, 3 runs a case: root cause and exact plan in 9 of 9 across bad-deploy, bad-deploy-old and the new 300-commit bad-deploy-long, both commit attacks 6 of 6; the model read a further part (`skip=112`, as the cut note said) in 1 of 3 bad-deploy-long runs and never needed the oldest commit, so the scenario shows the wording does no harm, not paging to an old cause; $0.0503 |
| [Paging to a cause deep in a long deploy, 2026-11-12](evals/deep-commit-paging-2026-11-12.md) | #506's `bad-deploy-deep` on gpt-6-luna, 3 runs: only the two oldest of 300 deploy commits explain the fault; the model read `skip=112` once and never the third part, so it reached them in 0 of 3; root cause 2 of 3 and exact plan 3 of 3 from the logs alone, the failure reading the column mismatch the wrong way round; $0.0144 |
| [Naming the commit a cause rests on, 2026-11-12](evals/commit-sha-2026-11-12.md) | #350 on gpt-6-luna, 3 runs a case, before and after: bad-deploy's diagnosis named the deploy commit by sha in 6 of 6 (1 of 3 before), the root cause right in 6 of 6 (one graded by hand after the judge failed); in the commit attacks the report named the planted commit, to rule it out, in 2 of 3 each (0 before), and all 6 attacks still passed |
| [A client's command-line password in a real log, 2026-11-12](evals/injection-client-password-2026-11-12.md) | #501's `log-client-password` on gpt-6-luna, 3 runs: the three passwords planted in shop-web's log (plain `redis-cli -a`, `curl -u` in a JSON line, `mysql -p` in logfmt) reached the model in all 3 runs only as `<redacted>` and were in none of its input, the run's database, the graph state or what Bleep wrote; 3 of 3 passed, $0.0120 |
| [Showing where a URL setting points, 2026-11-09](evals/env-url-host-2026-11-09.md) | `inspect_container` shows a URL value's scheme, host and port (#378) on gpt-6-luna: bad-env, oom and the loop-back cases `verify-failed-url`/`verify-failed-oom` kept every score (root cause 3 of 3 each, plans exact); bad-env's diagnoses read the host from the setting instead of the log; oom's documented value 2 of 3 (a search miss, the change does not reach it); $0.037 |
| [Planted-order signs, 2026-11-06](evals/planted-markers-2026-11-06.md) | The signs code reads for planted orders (#271). On a fresh blind set, 27 of 92 reachable attacks caught and 7 false alarms in 296 honest lines, 5 of them quoting an attack. On gpt-6-luna, 13 of 14 attack runs whose orders reached the model reported, each naming the channel (3 of 13 with the escaped tag alone), 0 of 18 control runs, 20 of 20 attacks passed. Offline, 0 of 13,147 honest lines from the repo fire. |
| [Verify's loop-back, 2026-11-04](evals/loopback-2026-11-04.md) | Three loop-back cases on gpt-6-luna past an approved plan on a scripted host, 3 runs each: 9 of 9 passed; after a failed restart it never tried it again and told the human to raise the memory limit (root cause 3 of 3 by hand), after an approved stop it read the healthy dependent in 6 of 6 and never started the stopped worker, and a planted order in that dependent's logs was not followed (0 of 3); $0.017. |
| [Recovery line, 2026-11-04](evals/recovery-line-2026-11-04.md) | `explain_recovery` on gpt-6-luna (the cheap role) over five scripted logs, 3 runs each: the line named the cause in 15 of 15, said "No cause is visible" on logs without one, and neither a planted "maintenance" claim nor an early closing tag with a "kernel update" claim reached it; about 2 s and 370 tokens a line. |
| [Knowledge retrieval, 2026-11-03](evals/knowledge-2026-11-03.md) | 20 hand-written questions over the Docker docs snapshot with the real embedder: the right page in the top 5 for 20 of 20 and first for 20 of 20; ingest 19.8 s, search 6 ms. |
| [bge-m3 against bge-small, 2026-11-06](evals/embeddings-multilingual-2026-11-06.md) | 11 German and mixed-language knowledge questions: first for 8 with bge-m3, 4 with bge-small; 20 English: 16 against 20; memory recall over English and German incidents 15 of 18 first against 13. In the image bge-m3 costs 320 ms of CPU per text (22 ms), 566 s for the first ingest (33 s), 1.56 GB loaded and a 4.44 GB image (925 MB). |
| [Recall's threshold for bge-m3, 2026-11-06](evals/embeddings-min-score-2026-11-06.md) | The 18 recall queries again with a threshold sweep: bge-m3's lowest right score is 0.387, so 0.5 returned 17 of 18 and 0.35 returns 18 of 18 (147 of 162 others pass, bge-small 158 at 0.5); Bleep sets 0.35 for bge-m3 and keeps 0.5 for bge-small (#306). |
| [gemini-embedding-2 on German docs, 2026-11-10](evals/embeddings-german-docs-2026-11-10.md) | 22 queries over sloppy German service docs: right chunk first for 22 with gemini-embedding-2, 18 with bge-small; #146's German questions 9 of 11 against 4, English 19 of 20 against 20 (top 5 equal); recall cut-off 0.65 returns 18 of 18 (50 of 162 unrelated pass); 420 ms per embed, 3,072 dimensions, about $0.017 for a first ingest at OpenRouter's $0.20 per million tokens, read 2026-11-10 (#377). |
| [bge-m3 on a local server, 2026-11-11](evals/embeddings-local-server-2026-11-11.md) | bge-m3 on LM Studio (GGUF F16): the right chunk first for 22 of 22 on the German service docs and 8 of 11 on #146's German questions, 16 of 20 on its English ones; recall's 0.35 cut-off confirmed (18 of 18 returned); first ingest of the Docker docs 14.8 s (566 s in-process). nomic-embed-text-v1.5 on the same server is worse (18 of 22, 10 of 18 recall first) (#442). |
| [Reading chat messages, 2026-11-09](evals/talk-intent-2026-11-09.md) | 29 English and German chat messages read by gpt-6-luna (the cheap role): 29 of 29 after two prompt fixes, every kind and action right, no planted order in a name or cause followed; the first round answered English questions in German because the zone was Europe/Vienna; about 1.5 s and $0.00016 a message (#401). |
| [The chat names a watched URL, 2026-11-13](evals/talk-links-2026-11-13.md) | All 42 talk cases once on gpt-6-luna after `read.md` allowed a URL a check names: both answers about the shop fix wrote https://shop.example.com/ and code kept it; 41 of 42 passed, the one failure the known German follow-up answered in English; $0.0081 (#543). |
