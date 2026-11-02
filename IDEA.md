# Project idea: Bleep, an on-call agent for Docker hosts

Status: 2026-11-02. Idea chosen, design document drafted at
`docs/superpowers/specs/2026-11-02-bleep-design.md` (approved 2026-11-02).
Nothing built. Name: Bleep.

One line: an on-call engineer for your Docker host that notices incidents, diagnoses them,
asks before acting, fixes, and remembers.

## Where this comes from

- Danny: software engineer with 2 years of experience, deepening his AI engineering skills,
  self-hosts his own apps on a server managed with Coolify (Docker underneath).
- An earlier idea (an agent running Danny's job hunt) was dropped because a hand-run Claude
  skill already covered it. That brainstorm ended on a useful filter:
  if a Claude skill run by hand can do it, it is not an agent project. The agent earns its
  place only when it runs on its own over time, holds state, uses tools in a loop and stops
  to ask the human at the right moments. This idea passes that filter.
- Quality bar: Danny's two previous projects on the Desktop.
  - `~/Desktop/molecula` (Danny's favourite): medicines chatbot with real 3D structures.
    FastAPI + LangChain agent with six tools over a prebuilt store, guard before the agent,
    source check after, local or cloud model, MCP server, Docker, dated eval reports, design docs.
  - `~/Desktop/ai-interview`: interview practice with a 3D talking avatar.
    Next.js 16 + FastAPI + LangChain, local STT/TTS, dev panel (model, effort, prompt, cost,
    guard log), benchmarks page, jailbreak tests, decision docs.
  - Pattern to repeat: real data, tools instead of guessing, a guard, evals with numbers,
    a strong visual hook, local-first option, Docker Compose, thorough docs with decisions.
- Both previous apps run on Danny's Coolify server. They are the first real workloads this
  agent watches. Danny is user number one.

## The problem

Self-hosting means being on call yourself. Something breaks at night, you find out from a
user, then dig through logs, recent commits and container state by hand to work out why,
and then decide whether to restart, roll back or change config. Coolify (and similar
platforms) deploys well but does almost nothing for operating what is deployed.

## The idea

An agent that watches a Docker host, detects incidents, reads logs and code, diagnoses,
proposes a fix, waits for the human's yes (Telegram), executes, verifies, writes an
incident report, and remembers it for next time.

Pillars:

1. **Runs on its own.** Watches Docker events and health over time, not in a chat. LangGraph
   with persisted state; an incident is a long-running graph run that may wait hours for a yes.
2. **Diagnoses, not just alerts.** Tools: container state and logs, host metrics, recent
   commits and diffs, docs. The agent says what broke and why, with evidence.
3. **Asks before acting.** Every write action (restart, roll back, redeploy, env change) is a
   human-in-the-loop interrupt. The proposal with reasoning goes to Telegram; the user
   replies yes, no, or an instruction; the graph resumes with that answer.
4. **Remembers.** Long-term memory of incidents, root causes, what fixed them and the user's
   decisions. Repeat incident: "same as 21 Oct, here is what fixed it."
5. **Guarded.** Allowlist of actions, destructive commands blocked, per-host rules
   ("never restart the database without asking"), log content treated as untrusted data
   (prompt injection), developer settings separate from the user UI.

Visual hook / demo: a dashboard with hosts, containers, incident timeline and the agent's
live reasoning. Demo moment: kill an app's backend on stage, watch the agent diagnose,
ask, recover, and report.

## Scope line (decided 2026-11-02)

- **In scope:** the operations layer on top of any Docker host. Detect, diagnose, propose,
  execute behind approval, roll back to a previous image, remember, report. Reading the
  Traefik/proxy logs to tell "app down" from "proxy down" is fine.
- **Out of scope:** being a deployment platform. No image builds, proxy or certificate
  management, domains, databases, backups, user management. Coolify, Compose or similar
  keep doing that. Deploying a Compose stack from a repo is a possible later step, not v1.
- **Docker is the core, Coolify is a plugin.** Docker on the host is ground truth and works
  when Coolify is down. Python Docker SDK over SSH, nothing installed on the server. Config
  Coolify owns (env vars) and redeploys go through the Coolify API when the plugin is on,
  because direct changes would be overwritten on the next deploy. Restarting a container
  directly is fine.
- **Runs for others too.** A demo stack of small apps with built-in failure modes ships with
  the project, so anyone with Docker Desktop can run the full incident-to-recovery loop
  without Danny's server.

## Tools

Core (v1):

- **Docker** over SSH: list, inspect, logs, stats, events, restart, roll back to previous image.
- **SSH shell** on the host, read-only commands (disk, memory, processes).
- **GitHub**: the app's repo, recent commits and diffs ("what changed" is often the answer).
- **Telegram**: send proposals and reports, receive yes/no/instructions.
- **Memory**: save and recall incidents and decisions.
- **Knowledge search**: Docker docs, the apps' own docs and runbooks, past incident reports.

Plugins (later, each one a bundle of tools sharing one connection, with settings, and a
read-or-act label per tool, switchable in the UI):

- Coolify (env vars, redeploy), Docker Hub / GHCR (image versions), Uptime Kuma (outside-in
  checks), Sentry (errors with stack traces), Discord or Slack (alternative approval channel).
- Second kind of plugin: any MCP server by URL, loaded through LangChain's MCP adapters.
  Coolify ships a read-only MCP server already. "Add plugin" = REST wrapper we built, or an
  MCP server anyone built.

## Scope we expect by design

- Clear purpose and target user: people who self-host on Docker; Danny first.
- Tone of incident reports (terse or chatty), choice of LLM and provider, model settings as
  sliders, in-app help.
- Token usage and cost per incident; short-term memory (the incident in progress) and
  long-term memory (history, decisions); external API tools (GitHub, Telegram, Coolify, Docker
  Hub); login with hosts per user and per-host rules; feedback on each proposal and
  diagnosis feeding memory; six or more tools with an enable/disable plugin UI; multi-model
  support; a security guard, with developer settings separate from the user UI.
- Agentic RAG over Docker docs, app repos, runbooks and past incidents; LangSmith or Langfuse
  observability (new for Danny); evaluation report (inject known faults into the demo stack,
  score root cause and proposed fix, with Ragas or DeepEval); learning from accepted and
  rejected proposals; external data sources (GitHub, Docker Hub, Coolify).

## Stack (my guess, matching the previous two projects; not yet confirmed)

Python 3.14 + uv, FastAPI, LangGraph (new) + LangChain, Next.js 16 + Tailwind + shadcn/ui,
models through OpenRouter with a local option, Docker Compose, SQLite for state and memory,
Python Docker SDK over SSH, python-telegram-bot or raw Bot API.

## Decisions from 2026-11-02 (answered by Danny)

1. Name: Bleep (British for pager), chosen 2026-11-02.
2. Every write action is gated behind human approval in v1. Per-host whitelist of safe
   automatic actions is a later feature.
3. The agent runs in Docker on Danny's laptop (anywhere with Docker later). Reaches hosts
   over SSH with a key; nothing installed on the hosts. The spare M1 MacBook is a test server
   (SSH host running the demo stack), not a requirement.
4. Detection: Docker events and health checks plus outside-in HTTP checks of public URLs.
5. Dashboard shows all services with health, incidents with live reasoning and reports,
   approvals. Telegram carries the same approvals.
6. Real scope: Danny's two servers (Coolify): Molecula, a blog, static sites, dkwebsite.de.
   Demo: a Compose stack with fault switches ships with the project.

## Next step for a new session

The design doc is the source of truth. Create the GitHub repo, then plan with superpowers:writing-plans and turn the plan into GitHub issues
per milestone, then build.
