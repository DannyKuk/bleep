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
    LLM["<b>Models</b><br/>OpenRouter · local"]
    DB[("<b>SQLite</b><br/>bleep.db<br/>checkpoints.sqlite")]
    API["<b>FastAPI</b>"]
    FE["<b>Next.js</b>"]
    SSH --> REG
    SOCK --> REG
    REG --> DET --> INC --> RUN --> GRAPH
    GRAPH --> TOOLS --> REG
    GRAPH --> LLM
    INC --> DB
    GRAPH --> DB
    REG --> API --> FE
    INC -->|events, live| API
    style HOSTS fill:none,stroke:#888,stroke-dasharray:5 5
```

| Piece | Path | What it does |
|---|---|---|
| **Hosts file** | `config/hosts.example.yaml`, `backend/src/bleep/hosts/config.py` | Which hosts to watch and how to reach them, per-host and per-service rules, URLs and protected containers. Secrets are references to env vars or files, never values. |
| **SSH session and engine** | `hosts/ssh.py`, `hosts/engine.py` | One asyncssh connection per SSH host forwards the remote Docker socket; the `docker` SDK talks to it. A socket host talks to a local endpoint, in the demo a filtered proxy. |
| **Host shell** | `hosts/shell.py` | The read-only command allowlist (exact argv templates) and host basics (disk, memory, load) for Linux and macOS. |
| **Registry** | `hosts/registry.py`, `hosts/discovery.py` | Each host's connection, reachability, containers grouped into services, and basics. |
| **Pull watcher** | `watcher/poller.py`, `watcher/events.py` | One tick per host every 15 s: reads Docker events from a cursor and the container list, and turns them into signals. A dropped host is a failed tick, not a death. |
| **URL checks and host probe** | `watcher/http_checks.py`, `watcher/host_probe.py` | Outside-in GET of each service's URLs every 30 s (status, latency, body text); disk, memory and load signals from each host reading. |
| **Detector** | `watcher/detector.py` | Pure decision logic: a signal becomes an incident, a self-recovery or nothing (grace window, repeated deaths, debounce, host thresholds). |
| **Watcher manager** | `watcher/manager.py` | Runs a supervised task group per host, feeds every signal to the detector, opens or attaches incidents, starts the runner for new ones, and logs self-recoveries with a one-line explanation. |
| **Incidents** | `incidents/repository.py`, `incidents/bus.py` | Incidents and their signals, one open per subject; the event bus stores every event, then fans it out to live subscribers. |
| **Runner** | `incidents/runner.py` | Runs each incident's graph in the background, started by the watcher manager for each newly opened incident, resumed after a decision or a restart (`recover_on_boot` at startup), and keeps the incident's status in step. |
| **Approvals** | `incidents/approvals.py`, `incidents/reminders.py` | Pending approvals in SQLite, decided once by whichever channel answers first; HMAC grants for approved plans; reminders that never approve. |
| **Notifier and Telegram bot** | `notify/`, `prompts/notify/` | The notifier the graph and the reminder loop call: it tells the dashboard bus and, when a bot token is set, Telegram. The bot long-polls Telegram; a button or a reply to a proposal becomes a decision, raced fairly against the dashboard. |
| **Incident graph** | `graph/` | LangGraph state, nodes and wiring, checkpointed per incident in `checkpoints.sqlite`. |
| **Read tools** | `tools/` | Docker reads and the host command tool for the model, each scoped to the incident's host. |
| **Guard** | `guard/` | Redacts secrets, strips control characters, caps size and wraps untrusted text before anything reaches a model; checks every proposed plan before a human sees it (`guard/plan_check.py`); `guard/log.py` records what it refused, blocked or changed in the `guard_log` table. |
| **Models** | `llm/` | One factory for OpenRouter and local models, structured output with a plain-JSON fallback, and a token and cost ledger with a daily cap. |
| **Prompts** | `prompts/` | Every prompt is a `.md` file loaded with `prompts.load(name, **vars)`. |
| **Store** | `store/` | SQLite with numbered migrations. |
| **API** | `main.py`, `api/`, `auth/` | Login with one admin password and hashed sessions; the read API over hosts and services; incidents with their findings, timeline and recoveries; the dashboard's decision on an approval; the live event stream (SSE). |
| **Dashboard** | `frontend/` | Next.js app: login; an overview of hosts, services and self-recoveries, polled; the incident list; and each incident's page with its live reasoning, timeline, diagnosis and the plan to approve, reject or instruct, streamed through a route handler (`app/api/stream/route.ts`). |
| **Compose** | `docker-compose.yaml`, `config/socket-proxy.cfg.template` | Runs Bleep; `--profile demo` adds the socket proxy so Bleep can watch this machine. |

Paths under `backend/src/bleep/` are given from there.

## One incident, start to end

| Stage | What happens | Code |
|---|---|---|
| 1. Detect | A container dies, a URL fails or a host crosses a threshold; the detector waits out the grace window and debounces, then asks for an incident. | `watcher/detector.py` |
| 2. Open | The incident is opened, or the signal attaches to the subject's open one. | `IncidentRepo.open_or_attach` |
| 3. Run | The runner tells the channels the incident opened (Telegram gets it whatever the run ends with), then starts the incident's graph thread in the background (`thread_id` = incident id). | `IncidentRunner.start`, `Notifier.incident_opened` |
| 4. Gather | Containers, the last 200 log lines of the affected ones through the guard, host basics, rules and similar past incidents. No model call. | `graph/nodes/gather.py` |
| 5. Investigate | The model reads with read-only tools, at most 8 calls a round; each result is guarded and wrapped once, and every call and result streams to the bus. A spent cost budget stops here with a notice. | `graph/nodes/investigate.py` |
| 6. Diagnose | Root cause, evidence, confidence; low confidence investigates once more, then goes on marked unsure, and Propose still asks the model. | `graph/nodes/diagnose.py` |
| 7. Propose | One to three allowlisted actions, or no action with exact instructions for the human; checked in code before anyone is asked, and a refused plan goes back to the model once. | `graph/nodes/propose.py`, `guard/plan_check.py` |
| 8. Approval | The target containers are snapshotted, the plan is stored as a pending approval, sent to the human and shown with Approve, Reject and "Tell Bleep something" on the incident's page; the run checkpoints and waits for a yes, a no or an instruction, through a restart if need be. The first answer wins; an instruction goes back to Propose; a pending approval is re-sent every 60 min and never approved by itself. | `graph/nodes/approval.py`, `ApprovalStore`, `ReminderLoop`, `IncidentRunner.resume` |
| 9. Report | The outcome is set; `resolved` is only ever claimed after verification. | `graph/nodes/report.py` |

Every node publishes `node_started` and `node_finished` on the bus, which stores them for the incident's timeline. On boot, `IncidentRunner.recover_on_boot` resumes every unfinished run except those waiting for approval, and delivers a decision that was recorded before a crash. The app starts the runner with the watcher manager, which calls `recover_on_boot` once at startup and starts a run only for a newly opened incident, and runs the reminder loop beside them. The runner, the graph, the reminders and the API share one `ApprovalStore`, so a decision from any channel is the one the run sees, also after a restart.

## Key design choices

One line each; the full reasoning and issue numbers are on the design page named after each group.

**Hosts** ([details](design/hosts.md))
- **Docker over SSH by forwarding the socket**, so a macOS host whose non-interactive `PATH` lacks `docker` works too ([Docker over SSH](decisions/docker-over-ssh.md)).
- **The demo host goes through a filtered socket proxy**, never a raw socket mount ([socket host](decisions/socket-host.md)).
- **Services are keyed by Coolify labels, else the Compose project, else the container**, and Bleep's own containers (`bleep.self=true`) are left out.
- **Host commands are exact argv templates run without a shell**; anything else is refused by code.

**Watcher** ([details](design/watcher.md))
- **A death that recovers within 60 s is not an incident**; three deaths in ten minutes are.
- **Open at once, attach within the 30 s debounce**, so there is never a second incident for one cause.

**Incidents** ([details](design/incidents.md))
- **One open incident per subject, enforced by a unique index.**
- **An unreachable host absorbs its services' signals.**
- **The bus persists before it fans out**, so the timeline never loses an event.

**Graph** ([details](design/graph.md))
- **One module per node, each owning its routing**; `build.py` only wires them.
- **Gather is deterministic and reads logs through the guard.**
- **Investigate loops model ↔ read tools inside one node, at most 8 calls a round**; the cap ends the round, a spent cost budget ends the run.
- **One checkpointed thread per incident**, resumed after a restart; runs waiting for a human stay waiting.
- **The checkpoint serializer rebuilds only the state's own models.**
- **The first decision wins by one `UPDATE ... WHERE status = 'pending'`**, and a grant is an HMAC over the incident id and a plan hash that covers the resolved container ids.
- **Reminders every 60 min, never an automatic yes.**

**Agent and models** ([details](design/agent.md))
- **One model factory for OpenRouter and local models**; structured output falls back to plain JSON for weak models.
- **A cost ledger with a daily cap** stops a runaway incident before the next call.

**Guard** ([details](design/guard.md))
- **All host text is redacted, cleaned, capped at 8,000 characters and wrapped as untrusted data with a random id.**
- **Env values never reach the model.**
- **Only the closing tag with the same random id ends untrusted data**, and the system prompt says so.
- **Every plan is checked in code**: allowlisted, enabled actions on the incident's host only, never a protected or Bleep container, at most 3 steps.

**Notifications** ([details](design/notify.md))
- **Telegram is optional**: without a token everything runs through the dashboard.
- **Only the configured chat is answered**; anything else is dropped and logged.
- **A decision from either channel goes through the same first-wins `resolve`**, and the loser is told who won.

**App** ([details](design/app.md))
- **One admin password and hashed session tokens in an HttpOnly cookie.**
- **The dashboard reaches the API through a Next.js rewrite**, so the cookie stays same-origin.
- **A dashboard decision names the approval it answers**; a late one gets 409 naming the channel that won.
- **The live stream is SSE with `id:` = seq**, so a reconnect replays from `Last-Event-ID`; a ping every 15 s keeps proxies from closing it.
- **The dashboard serves the stream from a route handler, not the rewrite**, because the rewrite gzips it and holds every event until the stream ends ([SSE through Next.js](decisions/sse-through-next.md)).
- **The incident page reads the history, then streams after its last seq, and keeps each event once by seq**; a step event reloads the incident's findings.

## Decisions

| Question | Answer |
|---|---|
| [Docker over SSH](decisions/docker-over-ssh.md) | Forward the remote socket through one asyncssh connection per host. |
| [Socket host](decisions/socket-host.md) | A filtered proxy with four rules, not a raw socket mount. |
| [SSE through Next.js](decisions/sse-through-next.md) | A route handler that pipes the stream with `no-transform`; the rewrite held every event until the end. |
