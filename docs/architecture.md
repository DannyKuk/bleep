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
    MEM["<b>Memory</b><br/>past incidents · facts"]
    DB[("<b>SQLite</b><br/>bleep.db<br/>checkpoints.sqlite")]
    API["<b>FastAPI</b>"]
    FE["<b>Next.js</b>"]
    SSH --> REG
    SOCK --> REG
    REG --> DET --> INC --> RUN --> GRAPH
    GRAPH --> TOOLS --> REG
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
| **Read tools** | `tools/` | Docker reads and the host command tool for the model, each scoped to the incident's host, and `recall_incidents` over memory. |
| **Act tools** | `tools/docker_act.py`, `guard/grant.py` | Restart, stop and start a container for one step of an approved plan; each checks the grant against the plan, then the target (host error, protected, Bleep's own), and acts on the approved container id. Never offered to a model. |
| **Long-term memory** | `memory/`, `tools/memory.py` | Every finished incident with the human's decision and feedback, embedded by a local model (bge-small through fastembed) or an OpenAI-compatible endpoint, and facts per service. Gather recalls the 3 most similar (the same service's first) and the facts; Report writes the record and learns facts from the human's instructions; a model change re-embeds on boot. |
| **Action log** | `incidents/action_log.py` | Execute's ledger in SQLite: each step `started` before its engine call and `done`, `failed` or `skipped` after, so a restart never runs a step twice. |
| **Guard** | `guard/` | Redacts secrets, strips control characters, caps size and wraps untrusted text before anything reaches a model; checks every proposed plan before a human sees it (`guard/plan_check.py`); hashes plans and signs and checks approval grants (`guard/grant.py`); cuts log text out of every report (`guard/report_check.py`); `guard/log.py` records what it refused, blocked or changed in the `guard_log` table. |
| **Models** | `llm/` | One factory for OpenRouter and local models, structured output with a plain-JSON fallback, and a token and cost ledger with a daily cap. |
| **Prompts** | `prompts/` | Every prompt is a `.md` file loaded with `prompts.load(name, **vars)`. |
| **Store** | `store/` | SQLite with numbered migrations. |
| **API** | `main.py`, `api/`, `auth/` | Login with one admin password and hashed sessions; the read API over hosts and services; incidents with their findings, timeline and recoveries; the dashboard's decision on an approval and feedback on the findings; a service's facts; the live event stream (SSE). |
| **Dashboard** | `frontend/` | Next.js app: login; an overview of hosts, services and self-recoveries, polled; the incident list; each incident's page with its live reasoning, timeline, diagnosis, the plan to approve, reject or instruct, a vote on both, and the report once the run ends, streamed through a route handler (`app/api/stream/route.ts`); and each service's page with its URLs, rules, facts and past incidents. |
| **Compose** | `docker-compose.yaml`, `config/socket-proxy.cfg.template` | Runs Bleep; `--profile demo` adds the socket proxy so Bleep can watch this machine, and the demo shop. |
| **Demo shop** | `demo/`, `config/hosts.demo.yaml` | A proxy, a web app, its database and a worker for Bleep to watch, and `demo/fault.sh`, which switches eight faults on and off ([demo](demo.md)). |

Paths under `backend/src/bleep/` are given from there.

## One incident, start to end

| Stage | What happens | Code |
|---|---|---|
| 1. Detect | A container dies, a URL fails or a host crosses a threshold; the detector waits out the grace window and debounces, then asks for an incident. | `watcher/detector.py` |
| 2. Open | The incident is opened, or the signal attaches to the subject's open one. | `IncidentRepo.open_or_attach` |
| 3. Run | The runner tells the channels the incident opened (Telegram gets it whatever the run ends with), then starts the incident's graph thread in the background (`thread_id` = incident id). | `IncidentRunner.start`, `Notifier.incident_opened` |
| 4. Gather | Containers, the last 200 log lines of the affected ones through the guard, host basics, rules, the 3 most similar past incidents (the same service's first) and the service's facts. No model call. | `graph/nodes/gather.py`, `MemoryStore.similar` |
| 5. Investigate | The model reads with read-only tools, at most 8 calls a round; each result is guarded and wrapped once, and every call and result streams to the bus. A spent cost budget stops here with a notice. | `graph/nodes/investigate.py` |
| 6. Diagnose | Root cause, evidence, confidence; low confidence investigates once more, then goes on marked unsure, and Propose still asks the model. | `graph/nodes/diagnose.py` |
| 7. Propose | One to three allowlisted actions, or no action with exact instructions for the human; checked in code before anyone is asked, and a refused plan goes back to the model once. | `graph/nodes/propose.py`, `guard/plan_check.py` |
| 8. Approval | The target containers are snapshotted, the plan is stored as a pending approval, sent to the human and shown with Approve, Reject and "Tell Bleep something" on the incident's page; the run checkpoints and waits for a yes, a no or an instruction, through a restart if need be. The first answer wins; an instruction goes back to Propose; a pending approval is re-sent every 60 min and never approved by itself. | `graph/nodes/approval.py`, `ApprovalStore`, `ReminderLoop`, `IncidentRunner.resume` |
| 9. Execute | Every target is read again. One gone, recreated or started again since approval runs nothing and goes back to Propose with a "state changed since approval" notice and a voided grant, and so does a step the guard refuses now (at most 3 re-plans a run). Otherwise the steps run in order through the act tools, each logged `started` before and `done` or `failed` after; a failed step skips the rest. After a crash a finished step is never repeated, and a started one is read on the host instead of run again. | `graph/nodes/execute.py`, `tools/docker_act.py`, `ActionLog` |
| 10. Verify | Waits 20 s, then up to 3 checks 10 s apart; ok only when every planned container is running and healthy and every planned URL answers. A failure goes back to Investigate at most twice, then the outcome is `unverified`. | `graph/nodes/verify.py` |
| 11. Report | The outcome is set in code (`resolved` only after verification) with its fixed text; the model writes the summary, cause and follow-ups from Bleep's record, anything copied from a log is cut out, and the report, with a timeline built from the stored events, goes to the bus and every channel, and into long-term memory with the human's decision and feedback; the human's lasting instructions become at most two service facts; the runner closes the incident once the report is checkpointed. | `graph/nodes/report.py`, `guard/report_check.py`, `Notifier.report`, `MemoryStore.add_report`, `MemoryStore.replace_report_facts` |

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
- **Every act step is logged `started` before its engine call and finished after it**, so a resumed run never repeats a finished step.

**Graph** ([details](design/graph.md))
- **One module per node, each owning its routing**; `build.py` only wires them.
- **Gather is deterministic and reads logs through the guard.**
- **Investigate loops model ↔ read tools inside one node, at most 8 calls a round**; the cap ends the round, a spent cost budget ends the run.
- **One checkpointed thread per incident**, resumed after a restart; runs waiting for a human stay waiting. Only the runner closes an incident, after the last checkpoint.
- **The checkpoint serializer rebuilds only the state's own models.**
- **The first decision wins by one `UPDATE ... WHERE status = 'pending'`**, and a grant is an HMAC over the incident id and a plan hash that covers the resolved container ids.
- **Reminders every 60 min, never an automatic yes.**
- **Execute re-reads every target before the first step and runs nothing if one changed since approval**; at most 3 trips back to Propose per run.
- **A report is mostly code**: fixed outcome text and a timeline from the stored events; the model writes the summary, cause and follow-ups, and never "fixed" unless verified.

**Memory** ([details](design/memory.md))
- **A local embedding model in the backend, baked into the image**: bge-small through fastembed on two CPU threads; an OpenAI-compatible endpoint is the option ([embeddings](decisions/embeddings.md)).
- **Vectors carry their model's name**, and a model change re-embeds on boot.
- **Memory is Bleep's own, trusted text, except quoted stretches**, which reach the model wrapped as untrusted; stored text is redacted and has its wrapper tags escaped.
- **The human's decision is remembered with the plan and the day**, so a rejected plan is not proposed again unannounced.
- **A thumbs up or down on the diagnosis or the plan goes into the incident's record in words**, whenever the human gives it.
- **Service facts are learned only from the human's own instructions**, at most two per incident, and every fact can be deleted on the service's page.

**Agent and models** ([details](design/agent.md))
- **One model factory for OpenRouter and local models**; structured output falls back to plain JSON for weak models.
- **A cost ledger with a daily cap** stops a runaway incident before the next call.

**Guard** ([details](design/guard.md))
- **All host text is redacted, cleaned, capped at 8,000 characters and wrapped as untrusted data with a random id.**
- **Env values never reach the model.**
- **Only the closing tag with the same random id ends untrusted data**, and the system prompt says so.
- **Every plan is checked in code**: allowlisted, enabled actions on the incident's host only, never a protected or Bleep container, at most 3 steps.
- **Act tools refuse without a grant for the exact plan**, recomputing its hash; they re-check the target with the plan check's rules and act on the approved container id, and no model is ever given them.
- **A report loses any 60-character stretch it shares with a log the run read.**

**Notifications** ([details](design/notify.md))
- **Telegram is optional**: without a token everything runs through the dashboard.
- **Only the configured chat is answered**; anything else is dropped and logged.
- **A decision from either channel goes through the same first-wins `resolve`**, and the loser is told who won.

**Demo** ([details](demo.md))
- **The demo shop comes in through `include`**, so `--profile demo` starts everything, and its containers are the service `bleep`.
- **Every fault leaves the Docker-side trace a real one would**: a crash loop, an OOM kill, an unhealthy container or a failing URL.

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
| [Embeddings](decisions/embeddings.md) | bge-small through fastembed, in-process on two threads: 11 ms and 22 ms of CPU per text, +268 MB of image. |
