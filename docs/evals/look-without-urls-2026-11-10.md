# A look at a service without URLs, 2026-11-10

> **Answer:** with a code-written line telling Investigate and Diagnose that a looked-at service has no URL in Bleep (#445), `openai/gpt-6-luna` ended a look at the healthy demo shop without URLs `healthy` in 3 of 3 runs, where a live look at the owner's shop the same evening (`shop #6`, before the fix) had hedged "external health remains unknown" and ended no action. The three scripted look cases stayed 9 of 9, a look at the shop with its URLs stayed healthy (1 of 1), and a new case, a service without URLs whose own log shows unhandled errors, was never called healthy: all three runs named the failing handler and its 500s, two in category `code` and one in `unknown` while unsure, which the scorer counts as a miss (2 of 3). The round cost $0.028.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `main` role) |
| Judge | Look cases: none, scored in code (`backend/evals/look.py`). Scenarios: Claude (Opus 5.5), by hand: [`scenarios-20261110-214736.grades.jsonl`](data/scenarios-20261110-214736.grades.jsonl), [`scenarios-20261110-215939.grades.jsonl`](data/scenarios-20261110-215939.grades.jsonl) |
| Cases | Look cases `coolify-healthy-no-url`, `coolify-healthy`, `coolify-url-500` (3 runs each) and `coolify-nourl-log-errors` (new, 3 runs); scenarios `look-healthy-no-url` (new, 3 runs) and `look-healthy` (1 run) |
| When | 2026-11-10, 21:44 to 22:05 UTC |
| Code | `fix/445-look-without-urls`: `6957d94` for the three look cases and both scenarios, `aae17e0` for `coolify-nourl-log-errors` (adds the case and tests; no prompt change) |
| Raw data | [`look-20261110-214439.jsonl`](data/look-20261110-214439.jsonl), [`look-20261110-220341.jsonl`](data/look-20261110-220341.jsonl), [`scenarios-20261110-214736.jsonl`](data/scenarios-20261110-214736.jsonl) ([batch report](scenarios-2026-11-10-8.md)), [`scenarios-20261110-215939.jsonl`](data/scenarios-20261110-215939.jsonl) ([batch report](scenarios-2026-11-10-9.md)) |
| Reproduce | `uv run --group evals python -m evals.look --repeat 3`; `uv run --group evals python -m evals.run --judge manual --only look-healthy-no-url --repeat 3` (in `backend/`, demo shop up, Bleep's backend stopped) |
| Cost | $0.028 by the runs' own `usd`: $0.0141 the nine look runs, $0.0071 the new case, $0.0050 `look-healthy-no-url`, $0.0014 `look-healthy` |

## Why this round

After #421 and #423 a look at a healthy service ends `healthy`, but a service without a URL check still lost it: the live look `shop #6` (inc_01M73P23BA9A57ZXFB) at the owner's shop, which has no URL in `config/hosts.yaml`, found every container running and the logs normal, then wrote "External health remains unknown because no configured URL checks or results were collected", marked itself unsure and ended no action (#445).

## Results

| Case | Kind | Result |
|---|---|---|
| `look-healthy-no-url` (demo shop, URLs removed for the run) | scenario | 3 of 3 `healthy` ("Healthy when checked, nothing to do"), not unsure |
| `look-healthy` (demo shop with URLs) | scenario | 1 of 1 `healthy` |
| `coolify-healthy-no-url` | look case | 3 of 3 |
| `coolify-healthy` | look case | 3 of 3 |
| `coolify-url-500` | look case | 3 of 3 |
| `coolify-nourl-log-errors` (new) | look case | 2 of 3 |

- **No URL, healthy:** "Nothing is wrong: all four shop containers are running, and shop-web, shop-db, and shop-worker logs show normal startup and successful work. No URL is …" (run 3). None hedged.
- **No URL, own log failing:** every run named `server/api/posts/index.get.ts` reading `slug` from an undefined value and the unhandled 500s; none called the service healthy. Run 1 kept the category `unknown` ("does not establish whether the underlying cause is a code defect or missing/invalid input or data") and was unsure; the scorer requires a failing case to leave `unknown`, so it fails. The look would not end `healthy` either way: #421 needs a diagnosis that is not unsure.

## Limits

- Three runs a case; the no-URL scenario removes the shop's URLs for the run (`no_urls: true`), the owner's real shop was not looked at again after the fix.
- The new case's miss is a category choice on a correct finding; the scorer's category gate is stricter than the healthy ending needs.
