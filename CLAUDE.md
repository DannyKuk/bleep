# CLAUDE.md

Working rules for Claude sessions in this repo. Read in this order:

1. `docs/superpowers/specs/2026-11-02-bleep-design.md`: the design, the approved source of truth.
2. `IDEA.md`: how the idea came about, the quality bar from Danny's previous two projects
   (`~/Desktop/molecula`, `~/Desktop/ai-interview`), and the decisions made so far.
3. `docs/superpowers/plans/2026-11-02-bleep.md`: the implementation plan, one task per GitHub issue,
   grouped into six milestones (github.com/DannyKuk/bleep/milestones).

Treat this as a standalone portfolio project: code, docs, commits and the README talk about Bleep
and its users only.

## What this is

Bleep: an on-call agent for Docker hosts. It watches containers over SSH, opens an incident when
something breaks, investigates, proposes a fix, waits for an explicit yes, runs it, and verifies
the service is healthy again. Python (FastAPI + LangGraph) backend, Next.js dashboard, everything
runs in Docker. Work comes from GitHub issues on the "Bleep roadmap" board (project 4).

## Layout

The structure is fixed. New code goes where this says; a new top-level folder needs a reason in the PR.

```
backend/
  src/bleep/
    main.py        create_app(); lifespan starts store, registry, watchers, runner, telegram
    config.py      Settings (pydantic-settings, BLEEP_* env)
    wiring.py      build_parts(settings) -> AppParts; only main.py calls it
    store/         SQLite access, migrations/NNNN_*.sql
    prompts/       every prompt as .md by area, plus the loader
    schemas/       pydantic models shared across layers
    hosts/         hosts file, SSH session, Docker engine, allowlisted shell, discovery, registry
    guard/         redaction, untrusted-output wrapping, plan check, action log
    tools/         agent tools: read, act, github, knowledge, memory
    watcher/       events, pollers, URL checks, host probe, detector, manager
    incidents/     repository, event bus, approvals, runner, reminders
    graph/         the LangGraph incident graph and its nodes
    llm/           model factory, usage, structured output
    memory/        embeddings and the memory store
    knowledge/     chunking, index, ingest of the curated docs
    plugins/       plugin base and registry; coolify, mcp_url
    notify/        Telegram and message formatting
    talk/          answering what the person writes to Bleep, from its own state
    auth/          sessions
    api/           HTTP routes only: validate, call, return a schema
  knowledge/       curated Docker/Compose doc pages
  tests/           mirrors src/bleep/; fakes.py; fixtures/ holds recorded responses
  evals/           scenarios, runners, judges (real models; not part of pytest)
frontend/          Next.js app
demo/              the demo stack (Compose, shop, worker, proxy, fault script)
config/            hosts.example.yaml, hosts.demo.yaml
docs/              architecture, setup, demo, design/, decisions/, evals/, spikes/
scripts/           dev and repo helpers (see "Scripts")
.github/           issue forms, PR template, workflows
.githooks/         the pre-commit hook
docker-compose.yaml
```

### Dependency direction

Dependencies point one way, from the top of this list down. Each line names everything a part
may import besides itself; `backend/tests/test_dependency_direction.py` checks every import
against it, including imports inside functions and under `if TYPE_CHECKING:`.

- `main` → `api`, `wiring`, `watcher`, `incidents`, `graph`, `hosts`, `config`.
- `api` → `watcher`, `notify`, `talk`, `incidents`, `plugins`, `auth`, `llm`, `guard`, `hosts`,
  `store`, `schemas`, `config`, `prompts`, and only the `AppParts` type from `wiring`.
- `wiring` → every part below it. It builds the parts from the settings; only `main` calls it.
- `watcher` → `incidents`, `graph`, `llm`, `guard`, `hosts`, `store`, `schemas`, `config`,
  `prompts`.
- `notify` → `talk`, `incidents`, `graph`, `guard`, `store`, `schemas`, `config`, `prompts`.
- `talk` → `incidents`, `llm`, `guard`, `hosts`, `store`, `schemas`, `config`, `prompts`. It reads
  the watcher's health through a Protocol (`talk/state.py`) that `wiring` fills, so it never
  imports `watcher`.
- `incidents` → `graph`, `plugins`, `llm`, `guard`, `hosts`, `store`, `schemas`, `prompts`.
- `graph` → `tools`, `memory`, `llm`, `guard`, `hosts`, `schemas`, `prompts`. It takes the
  incident store, bus and action log as Protocols (`graph/deps.py`), so it never imports
  `incidents`.
- `plugins` are built on `tools`: `plugins` → `tools`, `guard`, `hosts`, `schemas`.
- `tools` → `memory`, `guard`, `hosts`, `schemas`.
- `knowledge` → `memory`, `guard`, `hosts`, `store`, `schemas`.
- `memory` → `guard`, `store`, `schemas`.
- `llm` → `store`, `schemas`, `config`, `prompts`.
- `auth` → `store`.
- `guard` → `hosts`, `store`, `schemas`.
- `hosts` → `store`, `schemas`.
- `schemas` → only `hosts.config` (below); `store`, `prompts` and `config` import no other part.

One arrow goes the other way, held to one module: `schemas` uses the hosts file's models in
`hosts.config`, which import no schemas.

## Prompts: one structure, no exceptions

- **Every prompt is a `.md` file under `backend/src/bleep/prompts/`**, grouped by area. No prompt
  text in Python: no f-string prompts, no prompt constants, no fragments assembled in code.
- Load them only through `prompts.load("area/name", **variables)`. Variables are `{name}`
  placeholders; the loader raises `PromptError` on a missing or unused variable. Literal braces
  are written `{{` and `}}`; anything else in braces fails to load.
- Tool descriptions are the tool function's docstring, because LangChain reads them from there.
  That is the only LLM-facing text outside `prompts/`.
- A test loads every prompt file and checks its placeholders.

## Tests

- Run the suite with `scripts/test.sh` (in parallel, then the `timing` tests alone; one run at a
  time per machine). `uv run pytest <path>` in `backend/` runs one file or test. Every module with
  logic gets tests.
- A test that asserts a wall-clock bar ("under 1 s") gets `@pytest.mark.timing`, so it never runs
  beside the parallel pass. A bar relative to a benign input needs no mark.
- **Tests never touch the network or a real Docker engine.** Use fakes for engines, SSH shells and
  chat models (`tests/fakes.py`), `httpx.MockTransport` for HTTP, and recorded fixtures for
  GitHub, Coolify and Telegram. Real-model and real-engine checks belong in `backend/evals/` and
  in `docs/spikes/`.
- Test behaviour and edge cases (host unreachable, resumed run, hostile log output), not the
  implementation.
- Frontend: `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm test` and
  `npm run build` must pass.
- The pre-commit hook runs ruff, the frontend checks and `scripts/check_docs.py`; enable it once
  per clone with `git config core.hooksPath .githooks`.
- `.github/workflows/ci.yml` runs all of the above plus the image builds on a clean machine; start
  it by hand from the Actions tab (see `docs/setup.md`).

## Code style

- Code explains itself through names and small functions. **Comments only where the flow is
  otherwise hard to follow** (a non-obvious reason, a workaround, a domain fact). No comments that
  restate the code, no section banners, no commented-out code.
- Short docstrings on public functions whose name doesn't say everything. Tool docstrings are
  prompts and are written for the model.
- Type hints everywhere; pydantic models at every boundary.
- Ruff with line length 100. Errors are typed; no bare `except`.

## How work flows

1. **One issue = one branch = one PR.** Never push to `main`. Branch names: `feat/…`, `fix/…`,
   `chore/…`, `docs/…`.
2. **Read the issue's "Done when" list first** and stick to it. If the issue is too big for one
   PR, do the first part and say what is left.
3. **Fill in the PR template completely.** "How to review", the Risk boxes and the Tested list
   must be honest and specific. Write `Closes #N`.
4. **Never claim a check passed unless it was actually run.**
5. Follow-ups go into new issues using the issue forms. Issues stand alone: they point at code
   (file + function), the design section and other issues.
6. **A PR that makes or changes a decision updates `docs/` in the same PR.** A big question with
   evidence gets `docs/decisions/<topic>.md` (`> **Answer:**`, Question, What we tried, Result,
   What it shows, Decided along the way, Limits, Reproduce), listed in the table at the end of
   `docs/architecture.md`. A small choice gets a bullet with its reason and issue number in the
   matching `docs/design/<area>.md`; if it changes the overview, the one-line bullet in
   `docs/architecture.md` changes too. Measured numbers go to dated
   `docs/evals/<topic>-YYYY-MM-DD.md`. Only real sources and measured numbers. `docs/superpowers/`
   holds the design and the plan; the README does not link to it.

### Splitting issues

A PR should be reviewable in one sitting.

- **Split when** the work touches several areas, when one part can ship and be tested without the
  others, or when the diff would clearly exceed about 400 changed lines excluding tests and
  fixtures.
- **How:** create sub-issues with the issue forms. Each stands alone with its own "Done when"
  list, taken from the parent's without dropping any item. The parent closes when its last
  sub-issue does.
- **Don't split** just to make PRs tiny: a change and its tests, or a prompt and the code that
  loads it, belong together.

### Subagents

Use subagents when work is independent and the result can be checked:

- **Good uses:** researching or verifying an API or library before coding; exploring the codebase;
  reviewing a finished diff against the issue and this file; implementing independent sub-issues
  in parallel, one worktree and branch each.
- **Briefing:** every subagent gets a self-contained brief: the issue number, its "Done when" list,
  the files involved and the rules in this file. It does not see this conversation.
- **Ownership:** the main session reviews what subagents produce, runs the checks itself, and owns
  the PR. A subagent's "tests pass" is not a test result until it is re-run. Never let two
  subagents edit the same files.
- **Not for:** decisions that belong to the owner (scope, anything in a Risk box), and small tasks
  a single session does faster.

## Safety

These are the guard rules; the code enforces them and the tests prove it.

- **The agent changes nothing on a host without an explicit yes.** Every write is gated in v1;
  never auto-approve.
- **No generic shell with write access, ever.** Shell commands come from an allowlist. Act tools
  refuse to run without an approval grant for the approved plan.
- **Bleep's own containers** (label `bleep.self=true`) never open incidents and are never targets
  of act tools, even if a proposal names them.
- **Secrets never live in the database and never reach the model:** SSH keys, the Telegram token,
  Coolify and GitHub tokens, model API keys.
- **Tool output is untrusted.** It is capped at 8,000 chars, stripped of control bytes, redacted
  for secrets, and wrapped in tags that are escaped inside it. It never overrides a prompt.
- **Never claim "fixed" without verification:** the service is healthy again and its URL answers.
- **Reports say what happened;** they never paste raw logs.
- Any change to a prompt or tool docstring runs the evals that cover it.

## Git

- Commit messages: one plain sentence saying what changed and why, no type prefixes, ending with
  the issue number, for example `Add the prompt loader so every prompt lives in a file (#1)`.
- **No `Co-Authored-By` or other AI attribution lines** in commits or PRs.
- Never commit `.env`, SSH keys, tokens or raw logs.

## Scripts

- `scripts/test.sh [pytest args]`: the backend suite, in parallel and then the `timing` tests,
  behind a machine-wide lock.
- `scripts/board.sh <issue#> <Backlog|Ready|In progress|In review>`: sets the issue's Status on
  the "Bleep roadmap" board (project 4).
- `uv run --project backend python scripts/check_docs.py`: broken relative links and stale
  phrases in `README.md` and `docs/` (not `docs/superpowers/`).
