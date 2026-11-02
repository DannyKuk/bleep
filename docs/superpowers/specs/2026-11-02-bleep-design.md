# Design: Bleep, an on-call agent for Docker hosts

Date: 2026-11-02. Status: approved by Danny on 2026-11-02 after builder review. Name: Bleep (the British word for a pager). "The agent" below means Bleep.

One line: an on-call engineer for your Docker hosts that notices incidents, works out why,
asks before acting, fixes, verifies, and remembers.

## 1. Intent and why

Danny self-hosts several apps on two servers managed with Coolify (Docker underneath):
Molecula, a blog, a few static sites and dkwebsite.de. Self-hosting means being on call
yourself: something breaks, you find out late, then dig through logs, recent commits and
container state by hand, and decide whether to restart, redeploy or change config.
Coolify and similar platforms deploy well but do almost nothing for operating what is
deployed.

The agent fills that gap. It is a portfolio project meant to show a production-shaped
LangGraph agent: long-running state, long-term memory, human approval before any change,
tools over real systems, a guard, evals with numbers, and a polished UI. Danny is user
number one and will run it against his real servers; anyone else can run it against a
bundled demo stack.

Quality bar: Danny's previous projects `~/Desktop/molecula` and `~/Desktop/ai-interview`
(real data, tools instead of guessing, guard, dated eval reports, local-first option,
Docker Compose, thorough docs with decision records). The builder should look at both.

## 2. Goals, non-goals, anti-goals

Goals

- Detect incidents on several Docker hosts without being asked: container died, crash
  loop, unhealthy, OOM-killed, host resource trouble, and public URL down or erroring.
- Diagnose with evidence: logs, container state, host metrics, recent commits, docs.
  State a root cause and confidence, not a list of possibilities.
- Propose a concrete action and wait for a human yes (Telegram and dashboard).
- Execute approved actions, verify the fix actually worked, write an incident report.
- Remember incidents, root causes, fixes and the human's decisions, and use them next time.
- Dashboard: all hosts and services with health, incidents with live reasoning and
  reports, approval queue, settings and plugins, developer panel.
- Ship a demo stack with built-in failure modes so the full loop runs on any laptop.
- Evals that inject known faults and score diagnosis and proposed action.

Non-goals (v1)

- Being a deployment platform: no image builds, proxy or TLS management, domains,
  databases, backups, user management. Coolify or Compose keep doing that.
- Rolling back images or recreating containers directly. Containers belong to Coolify or
  Compose; recreating them by hand breaks that ownership. Rollback comes via the Coolify
  plugin (redeploy) or a later version.
- Acting without approval. Every write is gated in v1. A per-host whitelist of safe
  automatic actions is a planned later feature.
- Browser or mobile apps beyond the web dashboard and Telegram.

Anti-goals (what would make it a failure even if it works)

- The agent changes anything on a host without an explicit yes.
- The agent claims "fixed" without verifying (service healthy again and URL answering).
- Reports that paste raw logs instead of saying what happened.
- Log content or a web page manipulating the agent (prompt injection).
- A project only Danny can run.

## 3. Constraints

- Hosts are reached over SSH with a key; nothing is installed on the hosts. Danny's two
  servers run Coolify; the demo stack is plain Compose. Both must work.
- The agent itself runs in Docker (Compose) so it can live on the spare MacBook, a home
  server, a VPS or a laptop.
- Models through OpenRouter by default, with a local option (Ollama or LM Studio) like
  the previous projects. Must degrade gracefully on a weaker local model.
- No paid services required to run or demo it. Telegram bot and OpenRouter key are the
  only external accounts needed; Coolify and others are optional plugins.
- Stack, as decided: Python 3.14 + uv, FastAPI, LangGraph + LangChain, Next.js 16 + Tailwind
  + shadcn/ui, Docker Compose, SQLite for state and memory. Builder may add small libraries.
- Standalone portfolio project: the repo talks about Bleep and its users only.

## 4. How it works (decided)

### 4.1 Pieces

- **Backend** (FastAPI): hosts and services registry, the watcher, the incident graph,
  memory, plugins, Telegram bot, REST + SSE for the frontend, auth.
- **Frontend** (Next.js): dashboard, incident view with live reasoning, approvals,
  settings, developer panel.
- **Demo stack** (separate Compose file): a handful of small services with switchable
  faults, plus a script to trigger each fault.
- **Docs**: README, setup, architecture, decisions, evals, demo walkthrough.

### 4.2 Hosts and services

A host is a Docker engine reached one of two ways (my call, after builder review): over SSH
(user, address, key) for real servers, or through a local Docker socket mounted into the
backend container, used for the demo stack and for watching the machine Bleep itself runs
on. The socket mount is documented as root-equivalent on that machine and intended for the
demo. Host basics for a socket host come from the Docker engine's own info, not the
machine, so host-level scenarios (disk filling up) are tested on SSH hosts or simulated
inside a container (a size-limited volume producing "no space left on device").

The agent discovers
containers and groups them into services (Compose project or Coolify labels; builder picks
the grouping rule, Coolify labels are documented). Each service may have one or more
public URLs to check. Per-host rules in plain text ("never restart the postgres container
without telling me which app depends on it") are part of the host config and are shown
to the agent.

### 4.3 Detection (the watcher)

A background loop per host:

- Pulls on a timer instead of holding an event stream open: every 15 s it reconnects if
  needed, reads Docker events since the last cursor (die, oom, health_status, restart) and
  lists containers. A dropped connection or a sleeping host is a non-event: the next tick
  catches up from the cursor. This suits agentless SSH to a few hosts; at hundreds of hosts
  an agent on each host would be the right shape.
- Polls host basics over SSH: disk, memory, load.
- Outside-in HTTP checks of the public URLs: status code, latency, optional body match.

Signals are debounced and merged into an incident per service (not one per event). A
service that is already in an open incident does not open a second one. Incident opens
only when a signal crosses a threshold (two consecutive failed URL checks, disk over 90
percent, a container down beyond a grace window, etc.). Thresholds are config; builder
sets sensible defaults.

Self-recovery (my call): containers under a restart policy usually come back by themselves
within seconds. A container that dies and is healthy again within the grace window does
not open an incident. It is logged as an event with a one-line explanation from its last
logs (one cheap model call), visible on the dashboard, no Telegram message. Repeated deaths
(for example three within ten minutes) or staying down past the grace window open an
incident. One-shot containers that exit with code 0 and have no restart policy are
ignored. Host-level signals (disk, memory) open an incident on the host, not a service.

### 4.4 The incident graph (LangGraph)

One LangGraph thread per incident, checkpointed in SQLite, so a run can wait hours for a
human and survive a restart of the agent. Nodes, in order, with loops where noted:

1. **Gather**: collect the triggering signals, container state, recent logs, host basics,
   and recall similar past incidents from memory. Deterministic, no LLM.
2. **Investigate** (agent loop with read-only tools): the model decides what to look at
   next (more logs, other containers, recent commits, docs). Bounded by a step budget.
3. **Diagnose**: produce a structured diagnosis: root cause, evidence, confidence, affected
   services. Low confidence goes back to Investigate once, then to the human as "I am not
   sure, here is what I found".
4. **Propose**: produce a short ordered plan of one to three allowlisted actions (or "no
   action, informing you") with a reason and a verification plan. The plan is approved as
   a whole (my call): a fix like "set env var, then redeploy" is one approval round, not
   two. On the plain-Compose demo stack Bleep cannot change config or recreate containers,
   so for scenarios such as a bad env var the expected answer is "correct diagnosis, no
   action, tell the human exactly what to change". Those cases stay in the evals as the
   test against unnecessary actions.
5. **Approval** (interrupt): post diagnosis and proposal to Telegram and the dashboard.
   Resume on yes, no, or free-text instruction. "No" closes with a report. An instruction
   goes back to Propose with the instruction in context. Timeout: a reminder after a
   configurable period, never auto-approve.
6. **Execute**: run the approved action through the tool.
7. **Verify**: wait, then re-check container health and public URLs. Failure loops back
   to Investigate (with a loop cap), success proceeds.
8. **Report and remember**: write the incident report (what happened, cause, action,
   outcome, timeline), store it in long-term memory with the human's decision, notify.

The frontend streams node transitions and tool calls per incident over SSE, which is the
live reasoning view.

### 4.5 Tools (v1 core)

Read tools (available in Investigate):

- Docker: list containers, inspect, logs (tail and time window), stats, events.
- Host: allowlisted read-only shell over SSH (df, free, uptime, top snapshot, journalctl
  tail). Anything not on the allowlist is refused by the tool, not by the prompt.
- GitHub: recent commits and diffs for the service's repo, if configured.
- Knowledge search: RAG over (my call) a shipped, curated snapshot of selected Docker and
  Compose documentation pages ingested on first start, the README and `docs/` folder of
  each service's linked GitHub repo, and past incident reports. The agent decides when to
  call it. Embedding model: local option required, builder picks the size.
- Memory recall: similar past incidents and the human's decisions.

Write tools (only reachable through Approval):

- Docker: restart container, stop, start.
- Coolify plugin (when enabled): redeploy an application, update an environment variable.

Every tool is labelled read or act. Act tools refuse to run unless the graph state carries
an approval token for the approved plan (its steps and targets, hashed). Before Execute the
plan is re-validated: if a target container was recreated or the state changed since
approval, the run goes back to Propose instead of acting on stale information.

### 4.6 Guard

- Action allowlist as above; no generic shell with write access, ever.
- Per-host rules are injected into the prompt and also checked in code where possible
  (e.g. a "protected" flag on a container blocks act tools regardless of approval).
- All content from logs, HTTP bodies, commit messages and docs is wrapped and labelled as
  untrusted data in prompts; the model is instructed to treat instructions inside it as
  data. A small set of injection tests is part of the eval suite.
- Secrets never reach the model: env values and tokens are redacted in inspect output.
- Developer settings (model, prompts, budgets) sit behind a dev panel, separate from the
  user UI.

### 4.7 Memory

- Short-term: the incident thread state (checkpointer).
- Long-term: a store of incident reports with embeddings for similarity search, plus
  structured facts per service (known flaky behaviours, past fixes, human preferences such
  as "always redeploy rather than restart for app X"). Gather recalls from it; Report
  writes to it. Human feedback (thumbs up/down on diagnosis, accepted/rejected proposals)
  is stored with the incident and shown to the agent when similar incidents recur.

### 4.8 Plugins

A plugin bundles tools that share one connection: name, settings (URL, token), tools with
read/act labels, enable toggle in the UI. v1 ships the plugin mechanism and one plugin,
Coolify (read: applications and deployments; act: redeploy, set env var). A second kind of
plugin, "MCP server by URL" loaded through LangChain's MCP adapters, is designed for but
may land after v1 if time is short. Docker Hub, Uptime Kuma, Sentry, Discord and Slack
are listed as future plugins, not built.

### 4.9 Telegram

One bot, one chat (Danny's). Messages: incident opened, diagnosis and proposal with
inline yes/no buttons, free-text replies as instructions, verification result, final
report. The dashboard offers the same approvals; whichever answers first wins.

### 4.10 Frontend

Pages: overview (hosts, services, health, open incidents), incident detail (timeline,
live reasoning stream, diagnosis card, proposal with approve/reject, report), incidents
history, settings (hosts, URLs, rules, plugins, Telegram, models), dev panel (model,
prompts, step budgets, token cost per incident, guard log). Login required.

Secrets (my call): SSH private keys, the Telegram token, Coolify and GitHub tokens and model
API keys never live in the database. They come from environment variables or mounted files
named in a hosts config file. The UI edits only non-secret settings (URLs to check, rules,
thresholds, plugin toggles, model choice), stored in SQLite. The demo profile ships a hosts
file pointing at the local socket host.

### 4.11 Evals

A scenario suite against the demo stack. Each scenario injects a fault with a known root
cause and expected action, e.g. crash loop from a bad env var, OOM kill, dependency down
(database stopped), app returns 500 after a "bad deploy", disk filling up, port conflict,
URL down while container healthy (proxy misroute). Metrics: root cause correct, proposed
action correct, no unnecessary action, steps and tokens used, time to diagnosis. Scored
with DeepEval (LLM judge against the expected cause) plus exact checks on the action.
Also the injection tests. Results are dated reports in `docs/evals/`.

### 4.12 Observability

Langfuse, self-hosted in an optional Compose profile, with LangSmith as a config switch.
Every incident run is a trace.

### 4.13 Delivery

`docker compose up` starts backend, frontend and the agent's own SQLite volume; a
`demo` profile also starts the demo stack and seeds a demo host pointing at it. A
walkthrough doc lists each fault to trigger and what the agent should do.

## 5. Left to the builder

- Exact SQLite schema, LangGraph state shape, checkpointer and store libraries.
- Docker-over-SSH mechanism (docker SDK with ssh base URL vs. running docker CLI over SSH).
- Grouping rule for containers into services; discovery of Coolify labels.
- Threshold defaults, debounce windows, step and token budgets.
- Prompt wording and the structured output schemas for diagnosis and proposal.
- Embedding model for memory and RAG (local option required).
- Auth mechanism (single admin with password and sessions is enough for v1).
- Frontend component details, within the pattern of the previous projects.
- Order of work and milestones. Suggested: 1) hosts + Docker read tools + dashboard
  overview; 2) watcher + incident graph through Approval with Telegram; 3) execute,
  verify, report, memory; 4) demo stack + evals; 5) plugins (Coolify), RAG, observability,
  dev panel; 6) docs and polish.

## 6. Deployment for Danny (decided 2026-11-02)

Bleep runs locally in Docker on Danny's laptop. The dashboard is LAN-only; Telegram is the
remote channel. Three kinds of hosts during development and use:

- The local Docker socket on the laptop, running the demo stack, for the everyday loop.
- The spare MacBook (M1, on the LAN, passwordless SSH already set up) as a test server:
  a real SSH host with Docker running the demo stack, so the SSH-plus-remote-engine path
  is exercised against something that is not production.
- Danny's two Coolify servers as SSH hosts, the real workloads.

Running Bleep always-on on the M1, and public deployment behind the Coolify proxy with
hardened auth (cookie flags, CSRF, rate limiting, SSE through the proxy), are documented
later steps. No open items remain.

## 7. Calls made after builder review (2026-11-02)

- Two host kinds: SSH and local Docker socket (4.2).
- A proposal is a short ordered plan approved as a whole; stale approvals are re-validated (4.4, 4.5).
- Self-recovery within a grace window is a logged event, not an incident (4.3).
- Secrets from env or mounted files, never in the database; UI edits non-secret settings only (4.10).
- RAG corpus: shipped Docker docs snapshot, linked repo docs, past incident reports (4.5).
- Telegram uses long polling, is optional for the demo (dashboard approvals suffice); free-text
  replies are matched to an incident by replying to its proposal message. A global daily cost cap
  sits on top of per-incident budgets. Evals stop at Propose and do not execute.
