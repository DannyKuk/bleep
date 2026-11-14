# Propose told the service's source, 2026-11-14

> **Answer:** with the service's source in Propose's prompt (#572), `openai/gpt-6-luna` named no Coolify action and never said "redeploy" for the demo's Compose service in 3 of 3 runs: each plan was no action with an empty `could_run` and told the person to edit the Compose file and run `docker compose up -d`, where shop #10's plan had said "the enabled Coolify actions can apply it and redeploy `shop`". On the Coolify application the plan still named `coolify.set_env` and `coolify.redeploy` in 3 of 3 (2 of 3 on 2026-11-11). The Compose case still fails in full 3 of 3 on Diagnose's `unknown` category, as it did before #572. The two cases cost $0.0173.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `main` role) |
| Judge | none, scored in code (`backend/evals/look.py`) |
| Cases | `compose-env-closes`, `coolify-env-could-run` (3 runs each) |
| When | 2026-11-14 15:18 to 15:22 UTC |
| Code | `feat/568-look-case-env-value` at `71a392c9`, which holds this branch at `0c722ecb` (the prompt and code under test; the later `31ed0a7b` changes only how `compose-env-closes` is scored) |
| Raw data | [`look-20261114-151846.jsonl`](data/look-20261114-151846.jsonl) (the same round also ran #568's case) |
| Reproduce | `uv run --group evals python -m evals.look --only compose-env-closes,coolify-env-could-run --repeat 3` (in `backend/`) |
| Cost | $0.0173, 167,839 tokens (the runs' own `usd`) |

## Why this round

#572 changes Propose's prompt (a new source line and the rule that Coolify actions belong to a `coolify` service) and drops `coolify.*` from `could_run` in code. The round covers the case that asked for it (the demo's bad-env on a Compose service, with the Coolify actions enabled) and the one that must not change (a Coolify application whose env fix Bleep could run). Both were run on 2026-11-11 for #365 ([could-run](could-run-2026-11-11.md)), so that round is the baseline.

## Results

| Case | Right answer | Passed | Said it right | Could run right | Plan names a Coolify action or a redeploy | Read outside the service |
|---|---|---|---|---|---|---|
| `compose-env-closes` | failing | 0 of 3 | 0 of 3 | 3 of 3 | 0 of 3 | 0 of 3 |
| `coolify-env-could-run` | failing | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 1 of 3 |

"Plan names a Coolify action or a redeploy" is the case's `plan_must_not_say`, checked on `reason`, `human_instructions` and `could_run`. The runs were scored at `0c722ecb`; re-read against `31ed0a7b`'s added `coolify actions?` pattern, no reason or instruction says "Coolify" at all, so the count stays 0 of 3.

- **Compose service:** every plan is no action with an empty `could_run`, so the incident closes. The instructions say to check the Compose configuration, correct the host in `shop-web`'s `DATABASE_URL` or the network alias, and apply it with `docker compose up -d shop-web` (runs 1 and 2 add `shop-db`). All three diagnoses name `DATABASE_URL`'s host `db`, but hedge between a wrong host and a missing network alias and choose category `unknown`, which the look scorer fails on a failing case. That failure is Diagnose's and the same as on 2026-11-11 (0 of 3 said it right), not part of #572.
- **Coolify application:** all three name `TESTAPP_FAIL_AFTER_S=180`, category `config`, and plan no action with `could_run: [coolify.set_env, coolify.redeploy]`, so the incident waits for the person. Run 2 also read `coolify-proxy`'s log.

## Limits

- Three runs a case, one model. The baseline is the 2026-11-11 round on older code, not a run of the same commit without #572.
- The cases run on a scripted host without the Coolify plugin, and a look opens them, not the watcher; Propose's prompt is the same either way ([evals design](../design/evals.md)). `coolify-env-could-run` now carries the Coolify application labels (`source: coolify`); on 2026-11-11 it ran as a Compose project named like a Coolify app.
- The code drop of `coolify.*` from `could_run` never fired here: no run named one for the Compose service. Its behaviour is covered offline only (`tests/graph/test_propose.py`).
