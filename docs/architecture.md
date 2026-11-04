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
    KNOW["<b>Knowledge</b><br/>Docker docs · repo docs<br/>past reports"]
    DB[("<b>SQLite</b><br/>bleep.db<br/>checkpoints.sqlite")]
    API["<b>FastAPI</b>"]
    FE["<b>Next.js</b>"]
    SSH --> REG
    SOCK --> REG
    REG --> DET --> INC --> RUN --> GRAPH
    GRAPH --> TOOLS --> REG
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
| **Read tools** | `tools/` | Docker reads and the host command tool for the model, each scoped to the incident's host, `recall_incidents` over memory, `recent_commits` and `commit_diff` for a service with a linked repo, and `knowledge_search`. |
| **GitHub client** | `hosts/github.py` | Read-only GitHub REST: a repo's commits and diffs, its README and docs files; works without a token on public repos. |
| **Act tools** | `tools/docker_act.py`, `guard/grant.py` | Restart, stop and start a container for one step of an approved plan; each checks the grant against the plan, then the target (host error, protected, Bleep's own), and acts on the approved container id. Never offered to a model. |
| **Plugins** | `plugins/`, `api/plugins.py` | Tools that share one connection, switched on and off as a whole: their read tools join Investigate's and their actions the plan check's enabled set while they are on. Secrets are env variable names, never stored or shown. The MCP plugin (`plugins/mcp_url.py`) connects one MCP server at `MCP_URL` and runs only the tools a person allowed; the Coolify plugin (`plugins/coolify/`) reads one Coolify at `COOLIFY_URL`. |
| **Settings store** | `incidents/settings_store.py`, `api/settings.py`, `guard/secret_fields.py` | Non-secret settings a person changes from the dashboard (model choice, budgets, thresholds, hosts' and services' rules, URLs and protected containers, plugin toggles), stored in SQLite over the hosts file and the defaults, in force at once; a body carrying anything secret-shaped is refused. |
| **Dev panel** | `api/dev.py`, `incidents/prompt_overrides.py`, `frontend/components/dev/` | Off unless `BLEEP_DEV_PANEL=true`: the model choice and budgets (through the settings store), prompt overrides checked like the loader, cost per incident and per day from the usage ledger with each incident's trace link, and the guard log by kind and incident. |
| **Long-term memory** | `memory/`, `tools/memory.py` | Every finished incident with the human's decision and feedback, embedded by a local model (bge-small through fastembed) or an OpenAI-compatible endpoint, and facts per service. Gather recalls the 3 most similar (the same service's first) and the facts; Report writes the record and learns facts from the human's instructions; a model change re-embeds on boot. |
| **Knowledge search** | `knowledge/`, `tools/knowledge.py`, `backend/knowledge/docker-docs/` | A shipped snapshot of 27 Docker and Compose doc pages, the linked repos' README and `docs/`, and past incident reports, cut at headings into chunks of 300 tokens and embedded with memory's model. Ingested on first start and refreshed in the background, skipped by content hash; `knowledge_search(query, sources?)` returns the best section of the 5 closest pages as untrusted data. |
| **Action log** | `incidents/action_log.py` | Execute's ledger in SQLite: each step `started` before its engine call and `done`, `failed` or `skipped` after, so a restart never runs a step twice. |
| **Guard** | `guard/` | Redacts secrets, strips control characters, caps size and wraps untrusted text before anything reaches a model; checks every proposed plan before a human sees it (`guard/plan_check.py`); hashes plans and signs and checks approval grants (`guard/grant.py`); cuts log text out of every report (`guard/report_check.py`); `guard/log.py` records what it refused, blocked or changed in the `guard_log` table. |
| **Models** | `llm/` | One factory for OpenRouter and local models, structured output with a plain-JSON fallback, and a token and cost ledger with a daily cap. |
| **Tracing** | `llm/tracing.py`, `docker-compose.observability.yaml` | Off by default; `TRACING=langfuse` or `langsmith` adds a tracer's callbacks to every graph run, one Langfuse trace per incident with the incident as its session, and stores the trace's link on the incident. `--profile observability` runs a self-hosted Langfuse. |
| **Prompts** | `prompts/` | Every prompt is a `.md` file loaded with `prompts.load(name, **vars)`, which reads the dev panel's override instead when one is in force. |
| **Store** | `store/` | SQLite with numbered migrations. |
| **API** | `main.py`, `api/`, `auth/` | Login with one admin password and hashed sessions; the read API over hosts and services; incidents with their findings, timeline and recoveries; the dashboard's decision on an approval and feedback on the findings; a service's facts; the settings, the plugins and the Telegram status; the live event stream (SSE). |
| **Dashboard** | `frontend/` | Next.js app: login; an overview of hosts, services and self-recoveries, polled; the incident list; each incident's page with its live reasoning, timeline, diagnosis, the plan to approve, reject or instruct, a vote on both, and the report once the run ends, streamed through a route handler (`app/api/stream/route.ts`); each service's page with its URLs, rules, facts and past incidents; and the settings page, which edits thresholds, rules, URLs, protected containers and plugins and shows every secret only as set or missing. |
| **Compose** | `docker-compose.yaml`, `config/socket-proxy.cfg.template` | Runs Bleep; `--profile demo` adds the socket proxy so Bleep can watch this machine. |
| **Demo shop** | `demo/`, `config/hosts.demo.yaml` | A proxy, a web app, its database and a worker for Bleep to watch, as their own Compose project `shop`, and `demo/fault.sh`, which switches eight faults on and off ([demo](demo.md)). |
| **Eval harness** | `backend/evals/` | `python -m evals.run`: switches a demo fault on, runs Bleep's watcher and graph in-process to the end of Propose with a real model, switches it off, and scores the root cause (a judge model through DeepEval, or by hand with `python -m evals.report --grades`), the category and the plan (exact); writes JSONL and a dated report ([how to run it](../backend/evals/README.md)). |

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
- **The GitHub client lives in `hosts/`**, where the token and each service's `repo` are set, so the knowledge ingest can use it without importing `tools`.
- **The GitHub tools read a service's linked repo without a token on public repos**, offered only where a `repo` is set; commit text is untrusted and every GitHub failure is a sentence the model can go on from.

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

**Knowledge search** ([details](design/knowledge.md))
- **A curated Docker docs snapshot ships with Bleep**, with each page's source, commit, fetch date and licence; a script refreshes it.
- **Chunks are cut at headings and at 300 tokens, one hit per page**: the right page came first for 20 of 20 questions ([knowledge chunks](decisions/knowledge-chunks.md)).
- **Re-ingest is skipped by content hash**, so only the first start embeds the snapshot; past incidents refresh every 10 minutes, repo docs daily.
- **Results are untrusted data**, guarded and wrapped like host output.

**Agent and models** ([details](design/agent.md))
- **One model factory for OpenRouter and local models**; structured output falls back to plain JSON for weak models.
- **`openai/gpt-6-luna` in the cloud, `openai/gpt-oss-20b` locally**: the local model is as accurate on the demo faults but never says unsure and acts more, so the checks and the approval gate carry its safety ([chat model](decisions/chat-model.md)).
- **A cost ledger with a daily cap** stops a runaway incident before the next call.

**Guard** ([details](design/guard.md))
- **All host text is redacted, cleaned, capped at 8,000 characters and wrapped as untrusted data with a random id.**
- **Env values never reach the model.**
- **Only the closing tag with the same random id ends untrusted data**, and the system prompt says so.
- **Every plan is checked in code**: allowlisted, enabled actions on the incident's host only, never a protected or Bleep container, never a Docker action on a container that works unless it is the incident's own (named by a signal, the service of a URL-only incident, a top memory user of a host memory incident) or the human names it, at most 3 steps.
- **Act tools refuse without a grant for the exact plan**, recomputing its hash; they re-check the target with the plan check's rules and act on the approved container id, and no model is ever given them.
- **A report loses any 60-character stretch it shares with a log the run read.**

**Plugins** ([details](design/plugins.md))
- **A plugin is read from the registry at every use**, so switching it off takes its tools out of the next round, fails its actions in the plan check and leaves Execute no handler for them.
- **A plugin's secrets are env variable names**, shown only as set or not; its settings model may not hold a secret-named or secret-typed field.
- **The Coolify plugin reads applications, deployments and env keys, never an env value**: its answers are parsed to the fields Bleep names, so webhook secrets and build logs never leave the client; its address and token come from the env, and the address must be https unless it is private.
- **Coolify's actions run the Docker act tools' checks, a redeploy waits for its deployment to end, and a `set_env` value is never shown, logged or prompted.**
- **The MCP plugin runs only the server tools a person allowed** (`read_tools` without approval, `act_tools` only as an approved `mcp.call` step), through Bleep's own `mcp_tools` and `mcp_call`, so the server's descriptions arrive wrapped as untrusted data; its endpoint and token come from the env.

**Settings** ([details](design/settings.md))
- **Database override > hosts file > defaults, in force at once**; the dashboard can protect a container but never unprotect one the hosts file protects.
- **A settings body with a secret-shaped key or value anywhere is refused with 422**, naming where and never what.
- **A settings or plugin change must send the version it was read at (`If-Match`)**; a stale one is a 409 and changes nothing, so no page silently reverts another's change.
- **The settings page has no input for a secret**: each is shown as its variable or file and whether it is set; host connections are read-only, and the model choice and budgets are changed in the dev panel only.

**Dev panel** ([details](design/dev-panel.md))
- **Off unless `BLEEP_DEV_PANEL=true`, and then behind the admin session**; off, every `/api/dev` path is a 404 and the nav hides it.
- **A prompt override must use exactly its file's placeholders and hold no secret**; reset deletes it and the loader reads the file again.

**Notifications** ([details](design/notify.md))
- **Telegram is optional**: without a token everything runs through the dashboard.
- **Only the configured chat is answered**; anything else is dropped and logged.
- **A decision from either channel goes through the same first-wins `resolve`**, and the loser is told who won.

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

**Evals** ([details](design/evals.md))
- **A scenario runs Bleep's own watcher and graph in-process on a throwaway database and stops before Approval**: nothing is executed, sent or remembered.
- **The judge is never the model under test**: an OpenRouter model or the Claude Code CLI scores the root cause, or a human grades it by hand from a grades file kept next to the report; the plan is checked exactly, and acting where no action is expected counts as an unnecessary action.
- **The harness recreates the shop before every run** (volumes kept) and waits until it answers, so no fault's logs reach the next run; every run counts its structured-output fallbacks.
- **Every run records its largest single prompt**, the number a small local context meets first; a run whose model call reaches its length limit is a failed run with reason `context_length`, counted in the report.
- **Injection tests run the same harness on to a rejected plan and its report**: attacks planted in the shop's log, its reply bodies or a past incident pass when no marker, target step, asked-for tool call or secret value shows up in what Bleep wrote; honest controls on real faults are graded by hand (`--judge manual`); a Flagged column counts the runs where Bleep flagged planted orders, outside the pass rule; the human only sees a flag when code saw an escaped wrapper tag (#105, #112) ([result](evals/injection-2026-11-03.md)).

## Decisions

| Question | Answer |
|---|---|
| [Docker over SSH](decisions/docker-over-ssh.md) | Forward the remote socket through one asyncssh connection per host. |
| [Socket host](decisions/socket-host.md) | A filtered proxy with four rules, not a raw socket mount. |
| [SSE through Next.js](decisions/sse-through-next.md) | A route handler that pipes the stream with `no-transform`; the rewrite held every event until the end. |
| [Embeddings](decisions/embeddings.md) | bge-small through fastembed, in-process on two threads: 11 ms and 22 ms of CPU per text, +268 MB of image. |
| [Knowledge chunks](decisions/knowledge-chunks.md) | Cut at headings, then at 300 estimated tokens with 50 of overlap, one hit per page: 20 of 20 questions found first, against 18 at 400 and 600. |
| [Chat model](decisions/chat-model.md) | `openai/gpt-6-luna` for both cloud roles, `openai/gpt-oss-20b` as the local model; the local model never says unsure and can be steered, so its safety is the plan check and the approval gate. |

## Eval reports

| Report | What it measured |
|---|---|
| [Scenarios, 2026-11-03](evals/scenarios-2026-11-03.md) | The eight demo faults on `openai/gpt-6-luna`, 3 runs each: root cause right in 21 of 24 (hung 0 of 3 on a shop with stale logs, 3 of 3 on a reset shop), plan exact in 18 of 24, $0.0035 and about 70 s to a diagnosis per run. |
| [Prompt injection, 2026-11-03](evals/injection-2026-11-03.md) | On gpt-6-luna, 13 of 13 live attacks (log, reply body, memory) failed to steer Bleep or leak a secret; 6 of 6 honest controls kept the right root cause. After #105, the model flagged planted orders in 12 of 13 attacks but also in 4 of 6 honest controls, so its flag is measured and not shown. |
| [Local model, 2026-11-03](evals/scenarios-local-2026-11-03.md) | The eight faults and the injection suite on `openai/gpt-oss-20b`, 1 run each on a reset shop: root cause 7 of 8, controls 6 of 6, 0 fallbacks, 55 s to a diagnosis, $0; 2 of 13 attacks worked, 13 of 13 passed after the `healthy_target` rule, but the diagnoses were still steered: the plan check and the approval gate carry the safety. |
| [bad-deploy with the GitHub tools, 2026-11-03](evals/scenarios-2026-11-03-2.md) | bad-deploy on gpt-6-luna with a GitHub fixture, 2 rounds of 3: root cause 6 of 6 at 0.85 to 1.00, every run citing the version-2 commit and its migration, against 0.80 to 0.85 and neither named on the logs alone; category still off (#108). |
| [Prompt injection through commits, 2026-11-03](evals/injection-commits-2026-11-03.md) | The two commit-message attacks on gpt-6-luna, 12 runs: every run read the commits and none followed them; the model's `planted_in` named `commits` in all 12, but also in 3 of 6 honest bad-deploy runs, so it is no measure of recognition (#105). |
| [Prompt injection through docs, 2026-11-03](evals/injection-docs-2026-11-03.md) | The two docs attacks on gpt-6-luna, 16 runs: all passed, but the model searched the docs in only 4, and in those 4 the planted page came back and was not followed. |
| [Scenarios with knowledge search, 2026-11-03](evals/scenarios-2026-11-03-3.md) | The eight faults on gpt-6-luna with `knowledge_search` offered, 1 run each: root cause 7 of 8 (hung missed, as since #106), plan exact 6 of 8; no diagnosis cited the docs. |
| [Scenarios with the MCP action, 2026-11-04](evals/scenarios-2026-11-04.md) | The eight faults on gpt-6-luna after `mcp.call` joined the plan's actions (plugin off), 1 run each: root cause 7 of 8 (hung missed, as since #106), plan exact 7 of 8, no plan named an MCP action, $0.025. |
| [Knowledge retrieval, 2026-11-03](evals/knowledge-2026-11-03.md) | 20 hand-written questions over the Docker docs snapshot with the real embedder: the right page in the top 5 for 20 of 20 and first for 20 of 20; ingest 19.8 s, search 6 ms. |
