# Showing the health check's probes for a hung container, 2026-11-12

> **Answer:** with `inspect_container` showing the health check and Docker's last probes (#480), `openai/gpt-6-luna` proposed the expected `docker.restart shop-web` for the hung fault in 5 of 6 runs, against 0 of 3 before, where every plan was "no action". It still does not name the cause: the root cause passed in 1 of 6 runs (0 of 3 before) and the category was `unknown` in all 9. Every diagnosis after the change says the probes time out while the container keeps running; what it will not decide is whether shop-web's code hangs or its `/health` stalls on the database query or the uploads write that `/health` makes. Adding how long ago the container last wrote a log line did not move this in 3 runs. db-down and disk-full kept the root cause and an exact plan in both rounds. The three batches cost $0.044.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | `claude-code:opus` through DeepEval GEval; a root cause passes at 0.7 or more. Two judge calls failed (`KeyError: 'score'`, fixed since by #489); those runs are graded by hand below |
| Before | `main` at `8d4a4f70`: hung, 4 runs ([batch report](scenarios-2026-11-12-2.md)) |
| After, probes | the branch at `b5c16359` (rebased since as `9d969bac`): hung 3 runs, db-down and disk-full 1 each ([batch report](scenarios-2026-11-12-3.md)) |
| After, probes and log age | the branch at `c460f8d9` (rebased since as `4b6a5820`), the code of this PR: hung 3 runs, db-down and disk-full 1 each ([batch report](scenarios-2026-11-12-4.md)) |
| When | 2026-11-12 10:02 to 11:53 UTC |
| Raw data | [`scenarios-20261112-100249.jsonl`](data/scenarios-20261112-100249.jsonl), [`scenarios-20261112-104541.jsonl`](data/scenarios-20261112-104541.jsonl), [`scenarios-20261112-114329.jsonl`](data/scenarios-20261112-114329.jsonl) |
| Cost | $0.0102 + $0.0156 + $0.0181 = $0.044, the model under test; the judge is billed to a plan |

## Why this round

Bleep had never diagnosed the hung fault on the cloud model: 0 of 3 in [scenarios-2026-11-05-3](scenarios-2026-11-05-3.md), 0 of 1 in [scenarios-2026-11-11](scenarios-2026-11-11.md), and every honest-control miss in [injection-2026-11-05-2](injection-2026-11-05-2.md). The diagnoses named what they lacked: the health check's command and its per-probe results. `inspect_container` printed only `health: unhealthy`. #480 prints the check (command, interval, timeout, retries) and each recorded probe (time, duration, exit code, the start and last line of its output), and the tool's description says that a probe can time out inside its own command, as the demo's does: its `urlopen(..., timeout=3)` gives up before Docker's 5 s, so a hung shop-web shows exit 1 and `TimeoutError: timed out`, not Docker's exit -1. The description changed, so the faults that turn `unhealthy` ran again.

## Results

| Case | Runs | Root cause | Category | Plan exact | Confidence |
|---|---|---|---|---|---|
| hung, before | 3 (+1 lost) | 0 of 3 | `unknown` 3 of 3 | 0 of 3 (all no action) | 0.35 to 0.40 |
| hung, probes | 3 | 1 of 3 (0.70, 0.40, 0.40) | `unknown` 3 of 3 | 3 of 3 | 0.35 to 0.45 |
| hung, probes and log age | 3 | 0 of 3 (0.60, 0.50, 0.50) | `unknown` 3 of 3 | 2 of 3 | 0.40 to 0.45 |
| db-down, probes | 1 | 1 of 1 (0.90) | right | 1 of 1 | 0.91 |
| db-down, probes and log age | 1 | 1 of 1 (1.00) | right | 1 of 1 | 0.96 |
| disk-full, probes | 1 | 1 of 1 (0.90) | right | 1 of 1 | 0.97 |
| disk-full, probes and log age | 1 | 1 of 1 (by hand) | right | 1 of 1 | 0.83 |

Graded by hand where the judge failed:

- **hung before, run 1: fail.** "The cause of shop-web being marked unhealthy is not established: its logs show successful /health responses, but the live health-check configuration and failed probe results were not collected." No cause, the symptom only, as in runs 3 and 4 (0.30, 0.20).
- **disk-full with log age: pass.** "shop-web is failing health checks because its /uploads storage is full (Errno 28), causing the health endpoint to return 503." The expected cause, with the evidence; it leaves open whether the documented 20 MB cap or the volume's contents filled it, which the expected cause does not ask.

The lost run (hung before, run 2) timed out waiting for its incident: another eval recreated the shop's containers 28 s into it, which cleared the fault. It is left out of every count here.

## What it shows

- **The probes reach the model and change the plan.** Every diagnosis after the change cites the probes timing out while the container runs, 5 of 6 also a healthy shop-db and 3 of 6 CPU near 0; before, every diagnosis said the probe results were missing and the plan was "no action". The restart came in 5 of 6 runs, each with "I am not sure" and the reason in the plan, which is what the human sees before saying yes.
- **The cause stays open for a reason the evidence supports.** shop-web's `/health` queries the database and writes to the uploads volume (`demo/shop/docs/services.md`, which the model can search and did in 3 of 6 runs). A `/health` that stalls on either looks the same from outside as a hung process: running, probes timing out, nothing logged. The diagnoses say so: "a shop-web code hang and a failure involving its uploads volume or another dependency remain unresolved" (log age, run 2); "does not show whether the database check, uploads-volume write, or something else is stalled. A restart might only mask the symptom" (log age, run 3, the one no-action plan). The expected cause ("shop-web hangs") needs that doubt resolved, and the demo gives no evidence that resolves it.
- **The log age added nothing measurable.** With `last line written … ago` at the top of each log read, the root cause went from 1 of 3 to 0 of 3 and the plan from 3 of 3 to 2 of 3: within the spread of three runs, and no diagnosis used the age. It stays in because it is cheap, true and read from the engine's own clock; it is no fix for this fault.
- **db-down and disk-full did not pay for it.** Both kept the root cause and an exact plan in both rounds.

## Limits

- Three runs per case before and after, one model; one before run was lost, so before has three.
- The "after" rounds ran on the branch before it was rebased onto `main` at `b19c0afe`. The rebase brought #350, which changed one line of Diagnose's prompt about naming a commit by its sha (none of these faults involves a commit), and eval-harness changes (#489, #491, #476) that do not touch what the model sees.
- The judge failed on 2 of 13 diagnosed runs; both are graded by hand above, by the same model that wrote this report.
- Whether a shallower health check (one that touches no dependency) would let the model name the hang was not tried: that changes the demo, not Bleep.

## Reproduce

In `backend/`, demo shop up, Bleep's backend stopped. Before, on `8d4a4f70`:

```
uv run --group evals python -m evals.run --judge claude-code:opus --only hung --repeat 3
```

After, on this PR's commit:

```
uv run --group evals python -m evals.run --judge claude-code:opus --only hung --repeat 3
uv run --group evals python -m evals.run --judge claude-code:opus --only db-down --repeat 1 --append data/evals/scenarios-<stamp>.jsonl
uv run --group evals python -m evals.run --judge claude-code:opus --only disk-full --repeat 1 --append data/evals/scenarios-<stamp>.jsonl
```
