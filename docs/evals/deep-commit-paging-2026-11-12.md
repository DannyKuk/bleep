# Paging to a cause deep in a long deploy, 2026-11-12

> **Answer:** in `bad-deploy-deep`, where only the two oldest of a 300-commit deploy explain the fault, `openai/gpt-6-luna` reached them in 0 of 3 runs. One run read the second part (`skip=112`, as the cut note said) and none read the third (`skip=206`), which holds `Deploy shop 2` and the commit that reads `price_cents`. Every run diagnosed the fault from the logs alone: the missing `price_cents` column was named 3 of 3 times, the root cause passed in 2 of 3, and the plan was exact in 3 of 3. The failed run took the logs the wrong way round, saying the code should read `price`, which the old commits would have corrected. The round cost $0.0144.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | `claude-code:opus` through DeepEval GEval for the root cause (a pass is 0.7 or more) |
| Case | the new `bad-deploy-deep` scenario, 3 runs |
| Code | `feat/506-deep-commit-scenario` at `8bd4ebe7` |
| When | 2026-11-12 17:56 to 18:02 UTC |
| Raw data | [`scenarios-20261112-175612.jsonl`](data/scenarios-20261112-175612.jsonl) |
| Scenario report | [scenarios-2026-11-12-7](scenarios-2026-11-12-7.md) |
| Cost | $0.0144, the runs' own `usd`; the judge is billed to a plan |

## Why this round

The first `bad-deploy-long` round ([deployed-commits-paging-2026-11-12](deployed-commits-paging-2026-11-12.md)) could not show whether a model pages to an old cause. That deploy's newest commit (`d9f9be1`, `SHOP_VERSION` to 2) already explained the fault. `bad-deploy-deep` (#506) keeps the 300 commits, 41 days old, but its newest commit is a generated one that touches nothing of the shop. `Deploy shop 2` and the `price_cents` commit are positions 299 and 300 of 300. That puts them in the third part, so `deployed_commits` reaches them only with `skip=206`, after `skip=112`. Neither earlier part names the shop, `SHOP_VERSION`, `price` or a deploy.

## Results

| | Result |
|---|---|
| Root cause right | 2 of 3 (0.80, 0.60, 0.80) |
| Category `code` | 3 of 3 |
| Plan exact (no action) | 3 of 3; none proposed a restart |
| `deployed_commits` calls | 1, 1 and 2: run 3 read the first part and then `skip=112`; no run read `skip=206` |
| Diagnosis names a commit by sha | 0 of 3 |
| Tool calls | 8, 9 and 8 |
| Largest prompt | 15,532 tokens (run 2) |

0 structured-output fallbacks.

## What it shows

- **The model does not page to an old cause while the logs explain the fault.** All three diagnoses rest on the shop-web and shop-db logs that name the missing `price_cents` column. The model never went past the second part, and no diagnosis named a commit.
- **When it reads on, it follows the cut note.** The one further read used `skip=112`, the number the note printed, as in the `bad-deploy-long` round.
- **The old commits would have settled the direction of the mismatch.** Run 2 failed because it read the logs as code that should query `price`, which is how the shop's docs describe the table. The `price_cents` commit and `Deploy shop 2` show that version 2 reads `price_cents` on purpose and that the migration is what is missing. Runs 1 and 3 passed at 0.80 without saying why the column is missing.
- **Tool calls run short.** The runs made 8, 9 and 8 calls; reaching the third part costs two of them on top of the first `deployed_commits`.

## Limits

- One model, 3 runs.
- The logs Gather reads still name the missing column, so this scenario shows whether a model pages when the logs already give a plausible cause. It does not show what happens when the logs give no cause at all.
- The GitHub answers come from the fixture (`evals/fixtures/github/bad-deploy-deep.json`, built by `evals/long_deploy.py`); no run read GitHub itself.
- The raw scenario file cuts each tool call's arguments short, which is still enough to see `skip`.

## Reproduce

In `backend/`, with the demo shop up and Bleep's backend stopped:

```
export EVAL_JUDGE_MODEL=claude-code:opus
uv run --group evals python -m evals.run --only bad-deploy-deep --repeat 3
```
