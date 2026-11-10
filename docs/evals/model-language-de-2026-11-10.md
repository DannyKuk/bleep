# The model writes in German, 2026-11-10

> **Answer:** with `language: de` (#374), `openai/gpt-6-luna` wrote every diagnosis, plan reason and instruction in German, and left every name as the host wrote it: `shop-db`, `DATABASE_URL`, `WORKER_BATCH_MB`, `demo/compose.yaml` and the `docker compose … up -d` commands. Every plan field the code reads stayed English (`docker.start`, the targets, `no_action`). Root cause 5 of 5 by hand, category and plan exact 5 of 5, the same as the English baselines. In oom the documented value `WORKER_BATCH_MB=8` was given in 2 of 3 German runs; English gave it in 2 of 3 runs on 2026-11-09 and missed it in today's one English control run, so the miss is not the language. Every knowledge search was written in English. The round cost $0.019.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | Claude (Opus 5.5), by hand, root cause against each scenario's expected cause, and by reading: is the text German, are the names unchanged. Grades: [`scenarios-20261110-143823.grades.jsonl`](data/scenarios-20261110-143823.grades.jsonl), [`scenarios-20261110-144601.grades.jsonl`](data/scenarios-20261110-144601.grades.jsonl) |
| Cases | `de`: db-down 1 run, bad-env 1 run, oom 3 runs. English control: oom 1 run on the same commit |
| When | 2026-11-10 14:38 to 14:50 UTC |
| Commit | `51ab822` (#424, not merged at the time) |
| Raw data | [`scenarios-20261110-143823.jsonl`](data/scenarios-20261110-143823.jsonl) (`de`), [`scenarios-20261110-144601.jsonl`](data/scenarios-20261110-144601.jsonl) (English control) |
| Batch reports | [`scenarios-2026-11-10-3.md`](scenarios-2026-11-10-3.md) (`de`; its one-line summary says "1 run each", oom ran 3 times), [`scenarios-2026-11-10-4.md`](scenarios-2026-11-10-4.md) (English control) |
| Reproduce | In `backend/`, demo shop up, Bleep's backend stopped: `uv run --group evals python -m evals.run --language de --judge manual --only db-down --repeat 1`, then `--only bad-env --repeat 1` and `--only oom --repeat 3`, each with `--append data/evals/<that batch>.jsonl`; the control without `--language`. Grades applied with `python -m evals.report … --grades … --grader "Claude (Opus 5.5)"` |

## Why this round

#374 adds one placeholder to Diagnose, Propose and Report (and the recovery line) that, for `de`, says which fields to write in German and that names, keys, values, actions and targets stay exactly as they are. With `en` the prompts render byte for byte as before (a test), so the English numbers so far hold; this round asks whether German costs anything in what the scores measure, and whether a translated name or command slips into a plan.

## Scenarios

| Scenario | Runs | Root cause | Category | Plan exact | German text | Names unchanged | Gives the documented value | Cost |
|---|---|---|---|---|---|---|---|---|
| db-down | 1 | 1 of 1 | 1 of 1 | 1 of 1 (`docker.start shop-db`) | 1 of 1 | 1 of 1 | no setting involved | $0.0027 |
| bad-env | 1 | 1 of 1 | 1 of 1 | 1 of 1 (no action) | 1 of 1 | 1 of 1 (`DATABASE_URL`, `shop-db:5432`, `docker compose up -d shop-web`) | host `shop-db:5432`: 1 of 1 (not scored) | $0.0026 |
| oom | 3 | 3 of 3 | 3 of 3 | 3 of 3 (no action) | 3 of 3 | 3 of 3 (`WORKER_BATCH_MB`, `docker compose -f demo/compose.yaml up -d shop-worker`) | `WORKER_BATCH_MB=8`: 2 of 3 diagnoses, 2 of 3 plans | $0.0094 |
| oom, English control | 1 | 1 of 1 | 1 of 1 | 1 of 1 | n/a | 1 of 1 | `WORKER_BATCH_MB=8`: 0 of 1 | $0.0040 |

- **The German reads as German, not as translated English**, for example "Ändere in `demo/compose.yaml` die Einstellung `WORKER_BATCH_MB` für `shop-worker` von `256` auf den dokumentierten Wert `8`." The unsure oom run started its reason with "Ich bin nicht sicher", as the German Propose rule asks.
- **Every knowledge search was in English** (`WORKER_BATCH_MB`, "What value should WORKER_BATCH_MB be set to …"): Investigate's prompts are not translated, and Diagnose's `missing`, which becomes Investigate's next instruction, stays English since the review of #424.
- **The oom miss is the search, not the language.** The run that missed read the deploy docs' "Limits and restarts" section and not "Settings"; the English control missed the same way ("the repo docs search found no documented target value"). On 2026-11-09 English gave the value in 3 of 3 runs ([remedy lookup](remedy-lookup-2026-11-09.md)) and then in 2 of 3 ([env URL host](env-url-host-2026-11-09.md)).
- **`planted_in` names places with no planted text** in German oom (`logs`, `events`, `inspect`, `docs`) and once in German bad-env (`logs`), as English oom did on 2026-11-09 (`logs`, `events`, `inspect`). Not new with German; the values stayed English.
- 0 structured-output fallbacks.

## Limits

- Six German runs on three scenarios and one model. The other scenarios, the injection suite and the local model (`openai/gpt-oss-20b`) did not run in German.
- Scenario evals stop after Propose, so the German report, its `cause_en` for memory and the recovery line were not measured here; tests cover their shape. The live check of one whole German incident is #373's last item.
- German was judged by one reader, by hand.
