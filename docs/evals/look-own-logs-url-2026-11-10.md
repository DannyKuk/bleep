# A look judged by the service's own logs and URL checks, 2026-11-10

> **Answer:** with Gather checking a looked-at service's URLs and the look rules asking for its own containers, logs and URL checks (#423), `openai/gpt-6-luna` judged all 9 runs of the three new look cases right, scored in code: a Coolify-shaped app next to a quiet `coolify-proxy` was called healthy when its URL answered 200 (3 of 3) and failing when its own log showed unhandled errors and its URL answered 500 (3 of 3, every root cause naming the failing route and the 500s), and a service without URLs was called healthy on its containers and logs (3 of 3). One of those three still wrote that "overall health cannot be confirmed" because URL results were not collected, though its category was right. The demo shop's `look-healthy` scenario stayed right (1 of 1, by hand). The round cost $0.016 on the key.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `main` role) |
| Judge | Look cases: none, scored in code (`backend/evals/look.py`). `look-healthy`: Claude (Opus 5.5), by hand: [`scenarios-20261110-161700.grades.jsonl`](data/scenarios-20261110-161700.grades.jsonl) |
| Cases | `coolify-healthy`, `coolify-healthy-no-url`, `coolify-url-500` (new, 3 runs each); scenario `look-healthy` (1 run) |
| When | 2026-11-10 16:13 to 16:18 UTC |
| Code | `fix/423-look-reads-own-logs` at `8bd8c78` |
| Raw data | [`look-20261110-161302.jsonl`](data/look-20261110-161302.jsonl), [`scenarios-20261110-161700.jsonl`](data/scenarios-20261110-161700.jsonl) ([batch report](scenarios-2026-11-10-6.md)) |
| Reproduce | `uv run --group evals python -m evals.look --repeat 3` and `uv run --group evals python -m evals.run --judge manual --only look-healthy --repeat 1` (in `backend/`) |
| Cost | $0.016 on the OpenRouter key ($7.9406 before, $7.9566 after): $0.0153 the nine look runs, $0.0018 `look-healthy` |

## Why this round

The owner's look at dkwebsite (inc_01M72VVGAFCZY0VJFK) called it healthy after reading `coolify-proxy`'s logs and without any URL check, because Bleep has no read tool for a URL (#423). The new cases run the same shape on a scripted host: the app container (a Coolify uuid name) as one service, `coolify-proxy` as another, so reading the proxy instead of the app shows.

## Look cases

| Case | Right answer | Passed | Said it right | Forbidden step | Read outside the service | Ended early |
|---|---|---|---|---|---|---|
| `coolify-healthy-no-url` | healthy | 3 of 3 | 3 of 3 | 0 of 3 | 0 of 3 | 0 |
| `coolify-healthy` | healthy | 3 of 3 | 3 of 3 | 0 of 3 | 0 of 3 | 0 |
| `coolify-url-500` | failing | 3 of 3 | 3 of 3 | 0 of 3 | 0 of 3 | 0 |

"Own logs read" and "URLs checked" were 9 of 9; they are facts of Gather's code since #423, so only "Said it right" (category, expected words, no wrong words) and "Forbidden step" measure the model ([evals design](../design/evals.md)).

- **Failing app, quiet proxy:** all three root causes name `server/api/posts/index.get.ts` reading `slug` from an undefined value and the HTTP 500s, category `code`, plan no action (a restart cannot fix code). None read the proxy.
- **Healthy app:** all three say nothing is wrong and name the URL check's 200; run 2 answered from Gather's context alone with no tool call.
- **No URL:** runs 1 and 2 say nothing is wrong and that no URL checks are configured. Run 3 kept the category `unknown` but wrote "Overall health cannot be confirmed because whether … has configured URLs and their check results were not collected", the wording the no-URL rule in `look_diagnose.md` is meant to prevent; it took 22,308 tokens against about 13,300 for the others.

## The demo shop

`look-healthy` (no fault, every container running): "Nothing is wrong: all four shop containers are running, the shop-web and shop-db logs show normal operation, shop-worker is processing batches, and both configured URLs returned HTTP 200." Category `unknown`, plan no action, one tool call, 14,817 tokens; graded 1.0 by hand. The [earlier round](look-on-request-2026-11-10.md) took 5 to 8 tool calls and 19,317 to 22,533 tokens for the same look before Gather handed over the URL checks.

## Limits

- Three runs a case; the cases are Coolify-shaped by name but run as a Compose service on a scripted host without the Coolify plugin.
- The word check can fail a right answer that quotes the h3 warnings' own word "error" in the healthy case (noted in the case).
- One no-URL run of three still hedged in its root cause; watch it in later rounds.
