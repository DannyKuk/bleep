## What and why

<!-- One short paragraph: what changes for the user, and why. -->

Closes #

## How to review

<!-- A guide for the reviewer. Keep it honest: point at the risky parts. -->

**Read in this order:**
1. `backend/src/bleep/...`: what to look for here
2. 

**Look hard at:**
- 

**Safe to skim** (mechanical, renames, generated, moved code):
- 

## Risk

<!-- Tick what this PR touches and say how it's handled. Delete the lines that don't apply. -->

- [ ] Can change something on a host (act tools, approvals, the guard: the write path stays gated)
- [ ] Changes system prompts or tool docstrings (run the evals that cover them)
- [ ] Touches secrets handling (nothing secret reaches the database, the model or the logs)
- [ ] Changes the database schema (migration added and tested)
- [ ] Adds a dependency or changes a Dockerfile / `docker-compose.yaml`
- [ ] None of the above

## Tested

- [ ] `uv run pytest && uv run ruff check && uv run ruff format --check` (in `backend/`)
- [ ] `npm run format:check && npm run lint && npm run typecheck && npm test && npm run build` (in `frontend/`)
- [ ] `docker compose up --build`: <!-- what you did and what happened -->
- [ ] Evals: <!-- suite, scores before → after, or "not needed" -->

## Screenshots

<!-- For UI changes: before / after. Delete if not a UI change. -->

## Before merging

- [ ] Follow-ups are filed as issues (not left only in docs)
- [ ] README / docs updated where behaviour changed
- [ ] A design decision made or changed here is in the docs: a topic doc in `docs/decisions/` or a bullet in `docs/design/<area>.md` (and its one line in `docs/architecture.md` if the overview changes)
- [ ] No API keys, `.env` files, SSH keys or raw logs committed
