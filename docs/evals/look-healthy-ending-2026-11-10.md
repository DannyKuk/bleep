# A look at a healthy service ends healthy, 2026-11-10

> **Answer:** with #421, a look on request at the healthy demo shop ended `healthy` in 3 of 3 runs: `openai/gpt-6-luna` diagnosed "nothing is wrong" (category `unknown`, not unsure), proposed no action, and the code check at Report (the shop's own containers against Gather's reading, its hosts-file URLs answering) passed, so each report's outcome was "Healthy when checked, nothing to do" with no follow-ups, under the title "look requested on shop" (the service's name, not its id). Root cause 3 of 3 by hand, plan exact 3 of 3, $0.0041.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `main` role) |
| Judge | Claude (Opus 5.5), by hand: [`scenarios-20261110-174307.grades.jsonl`](data/scenarios-20261110-174307.grades.jsonl) |
| Scenario | `look-healthy` (no fault, every shop container running), 3 runs |
| When | 2026-11-10, 18:18 to 18:21 UTC |
| Code | `feat/421-healthy-look` at `e0b8eea` (main with #423 and #422 merged in) |
| Raw data | [`scenarios-20261110-174307.jsonl`](data/scenarios-20261110-174307.jsonl) ([batch report](scenarios-2026-11-10-7.md)) |
| Reproduce | `uv run --group evals python -m evals.run --judge manual --only look-healthy --repeat 3` (in `backend/`, demo shop up, Bleep's backend stopped) |
| Cost | $0.0041 (the runs' own `usd`) |

## Runs

| Run | Diagnosis | Outcome | Title | Follow-ups |
|---|---|---|---|---|
| 1 | "Nothing is wrong with shop: all four shop containers are running, its recent service logs show no failures, and both configured URL checks returned HTTP 200." | Healthy when checked, nothing to do | look requested on shop | none |
| 2 | "Nothing is wrong: shop-proxy, shop-web, shop-db, and shop-worker are running, their recent logs show normal startup and activity without errors, and both configured URL checks passed with HTTP 200." | Healthy when checked, nothing to do | look requested on shop | none |
| 3 | "Nothing is wrong: all four shop containers are running, the shop-web logs show successful startup and requests without errors, and both configured URL checks passed with HTTP 200." | Healthy when checked, nothing to do | look requested on shop | none |

Two runs answered from Gather's context with no tool call (10,315 and 10,500 tokens), one made one (14,317). Reports 2 and 3 named the service by its id `demo/compose:shop` in the summary the model writes; the title and the Telegram end use the name.

## Limits

- One healthy service, three runs. A failing look and the rules that keep a look from ending healthy (a failed URL at Gather, a restart since Gather, an unsure diagnosis, another service's container) are covered by unit tests with a fake engine, not by this round.
