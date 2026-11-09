# Showing where a URL setting points, 2026-11-09

> **Answer:** with `inspect_container` showing the scheme, host and port of a URL-shaped env value (#378), `openai/gpt-6-luna` kept every score it had: bad-env 3 of 3 root causes, oom 3 of 3, both loop-back cases 6 of 6, every plan exact, nothing forbidden. The diagnoses now read the setting itself ("DATABASE_URL names host `db`") instead of inferring it from the log, and no run hedged about the host any more; one run still said the credentials and database name are hidden, which they are. The scores could not rise: on the demo shop the log already names the wrong host, so the root cause passed before as well. oom gave the documented `WORKER_BATCH_MB=8` in 2 of 3 runs (3 of 3 before); its env value is not a URL, so the change does not reach it, and the miss is a search with the bare key that did not find the docs.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | Claude (Opus 5.5), by hand: [`scenarios-20261109-141927.grades.jsonl`](data/scenarios-20261109-141927.grades.jsonl), [`loopback-20261109-141141-main.grades.jsonl`](data/loopback-20261109-141141-main.grades.jsonl), [`loopback-20261109-141141-378.grades.jsonl`](data/loopback-20261109-141141-378.grades.jsonl) |
| Cases | Scenarios bad-env and oom, 3 runs each, after the change; loop-back `verify-failed-url` and `verify-failed-oom`, 3 runs each, before and after |
| Before | Scenarios: the round of the same morning, [`scenarios-2026-11-09-3.md`](scenarios-2026-11-09-3.md) on `a47a727`, whose tools and prompts equal `main` at `d4e7487` (only watcher code changed between them). Loop-back: run again on `d4e7487` |
| After | `1c03e15` (#385, not merged at the time) |
| When | 2026-11-09 14:11 to 14:25 UTC |
| Raw data | [`scenarios-20261109-141927.jsonl`](data/scenarios-20261109-141927.jsonl), [`loopback-20261109-141141-main.jsonl`](data/loopback-20261109-141141-main.jsonl), [`loopback-20261109-141141-378.jsonl`](data/loopback-20261109-141141-378.jsonl) |
| Cost | $0.037 for all 18 runs (OpenRouter key usage before and after) |

## Why this round

A live bad-env incident on the demo shop (two incidents, 2026-11-09) ended in the right "no action", but both diagnoses said the effective `DATABASE_URL` was unknown and the instructions told the human not to change it "unless inspection confirms it is wrong": `inspect_container` showed every env value as `KEY=<redacted>`. #378 shows the scheme, host and port of a value from a common network-service scheme whose host does not look like a token, and changes the tool's description, which is a prompt, so the scenarios that read env values run again.

## Results

| Case | Runs | Root cause before | Root cause after | Plan exact after | Names (`must_name`) after | Gives the documented value after |
|---|---|---|---|---|---|---|
| bad-env | 3 | 3 of 3 | 3 of 3 | 3 of 3 | `DATABASE_URL`: 3 of 3 | `shop-db:5432`: 3 of 3 (not scored) |
| oom | 3 | 3 of 3 | 3 of 3 | 3 of 3 | `WORKER_BATCH_MB`: 3 of 3 | `WORKER_BATCH_MB=8`: 2 of 3 (before 3 of 3) |
| verify-failed-url (loop-back) | 3 + 3 | 3 of 3 | 3 of 3 | 3 of 3, no restart | | |
| verify-failed-oom (loop-back) | 3 + 3 | 3 of 3 | 3 of 3 | 3 of 3, no restart | | |

The full scenario tables are in [the batch report](scenarios-2026-11-09-4.md).

## What it shows

- **bad-env reads the setting.** Before: "shop-web's DATABASE_URL points to host `db`, which does not resolve" (from the log line `failed to resolve host 'db'`). After: "shop-web's DATABASE_URL names host `db`" and, in run 2, "the current setting's database name and credentials are redacted, so do not infer them". The plan's instructions name the file and the fix ("a wrong DATABASE_URL hostname in the plain Compose configuration") in 2 of 3 runs; run 1 still says it is "not sure" of the full connection string, which is true, since the credentials stay hidden.
- **verify-failed-url gets easier, as expected.** Its scripted `DATABASE_URL` is `postgres://shop@shop-db-old:5432/shop`; after the change the host `shop-db-old` is shown, and one run of three said the host should be `shop-db` "(the collected URL uses port 5432)". Before, the log already named `shop-db-old`, so the root cause passed in every run either way.
- **oom is untouched.** `WORKER_BATCH_MB=256` is not a URL and stays hidden as before. The one run that missed the documented 8 searched `knowledge_search("WORKER_BATCH_MB", sources=["repo"])` and concluded the docs give no value; the two that found it asked a question ("what value should it be set to").
- **bad-env used more tokens**: bad-env 33k to 57k tokens per run after (the morning's runs about 27k each), driven by more `inspect_container` and `knowledge_search` calls, not by the env lines.

## Limits

- Three runs per case, one model. The scores were at their ceiling before, so this round can show no regression, not an improvement in the scores; the improvement is in the wording of the diagnoses, read by hand.
- The demo shop's bad-env log names the wrong host, so the scenario cannot measure a fault whose host appears only in the env. A scenario whose log says only "connection refused" would.
- The scenario "before" is the morning's round on `a47a727`, not a fresh run on `d4e7487`; tools, prompts and the graph are the same in both.

## Reproduce

In `backend/`, demo shop up, Bleep's backend stopped, on the PR's commit:

```
uv run --group evals python -m evals.run --judge manual --only bad-env --repeat 3
uv run --group evals python -m evals.run --judge manual --only oom --repeat 3 --append data/evals/scenarios-<stamp>.jsonl
uv run --group evals python -m evals.loopback --only verify-failed-url,verify-failed-oom --repeat 3 --judge manual
```

The loop-back "before" runs the last command on `main` (`d4e7487`).
