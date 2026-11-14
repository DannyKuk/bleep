# A setting's value past a long label block, 2026-11-14

> **Answer:** on the new look case shaped like #482's second Coolify run (#568), `openai/gpt-6-luna` read `HOST=127.0.0.1` from `inspect_container` and named it in the diagnosis with that tool as its source in 3 of 3 runs, though the value sits at character 2,539 of the guarded result, past the 1,500 at which Diagnose cut every message before #561. All three chose category `config` and planned no action with `could_run: [coolify.set_env, coolify.redeploy]`, so the incident waits for the person, and all three passed in full. The case cost $0.0089.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `main` role) |
| Judge | none, scored in code (`backend/evals/look.py`) |
| Case | `coolify-env-value-past-labels` (new, 3 runs) |
| When | 2026-11-14 15:22 to 15:24 UTC |
| Code | `feat/568-look-case-env-value` at `71a392c9` (main with #561, plus #572's source line, which this case needs to be a Coolify application) |
| Raw data | [`look-20261114-151846.jsonl`](data/look-20261114-151846.jsonl) (the same round also ran #572's two cases, [report](propose-source-2026-11-14.md)) |
| Reproduce | `uv run --group evals python -m evals.look --only coolify-env-value-past-labels --repeat 3` (in `backend/`) |
| Cost | $0.0089, 81,032 tokens (the runs' own `usd`) |

## Why this round

#561 let Diagnose read each guarded tool result whole. Its round ([env-value](env-value-2026-11-13.md)) had no case with a long label block before the env, the value in the env only and no docs on the key, so it could not show the fix itself. This case has all three, and a pass needs the model to have called `inspect_container` on the app's own container: a model that guessed `HOST` from the log's "Listening on http://127.0.0.1:3000" fails.

## Results

| Case | Right answer | Passed | Said it right | Could run right | Named the value | Forbidden step | Read outside the service |
|---|---|---|---|---|---|---|---|
| `coolify-env-value-past-labels` | failing | 3 of 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 1 of 3 |

- Every run called `inspect_container` on the app's container, and every diagnosis has an evidence entry with source `inspect_container` saying the container has `HOST=127.0.0.1` and that its proxy labels forward to port 3000.
- The root causes say the app listens on loopback only, so the proxy's requests to port 3000 get a 502. Each adds that the documented bind address was not found (the case has no docs on `HOST`), and the plan leaves the value to the person through `could_run`, as #561's `could_run` rule asks.
- Run 1 also read `coolify-proxy`'s log; run 3 read the container's stats.

## Limits

- Three runs, one model, no baseline: the case is new, and a run of it on code from before #561 was not made.
- The case runs on a scripted host without the Coolify plugin, and a look opens it, not the watcher ([evals design](../design/evals.md)). It has the Coolify application labels (`source: coolify`), so discovery groups it as one.
- It measures the value reaching the diagnosis and the plan, not a fix: the run stops at the plan.
