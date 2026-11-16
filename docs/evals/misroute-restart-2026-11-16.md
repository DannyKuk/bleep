# Restarting a proxy whose bad config survives a restart, 2026-11-16

> **Answer:** a missing fact and a prompt gap together. `inspect_container` never showed a container's start command, so none of the six models in the #616 round could see that shop-proxy runs `caddy run --resume`, which starts from the last config Caddy loaded. Every run inspected shop-proxy. The three that proposed `docker.restart shop-proxy` (gemini-3.8-flash, Claude Haiku 5.5, Claude Sonnet 5.5) used Caddy's default behaviour: they said a config loaded through the admin API "lives only in memory" and that a restart reloads the Caddyfile. The three that proposed no action were guessing too, just the other way: gpt-oss-20b blamed a "baked Caddyfile", Qwen said a restart "would likely reload the same faulty configuration", and luna said it "would reload the same misroute" without anything it had read showing that. Propose also had no rule about a config loaded while the container runs. #642 adds the start command to `inspect_container`. It also asks Diagnose to quote such a container's start command in its evidence, and tells Propose that a restart fixes a config loaded at runtime only when the evidence shows the container starts from the right one. In the eval round below, Haiku went from a restart to no action in 3 of 3 `misroute` runs; luna proposed no action in 2 of 3, and in the third read `--config` over `--resume` and restarted; `db-down` and `bad-env` held on both. #645 then added one sentence to Propose's rule: a config file named in the start command does not show what a restart loads when the same command also has a flag or state volume that may start the program from its last saved state. After it, luna proposed no action in 3 of 3 `misroute` runs, each time because of `--resume`, and `db-down` held.

## Question

In the #616 round ([models-2026-11-16](models-2026-11-16.md)), three of six models diagnosed `misroute` correctly and then proposed `docker.restart shop-proxy`. Sonnet did the same on 2026-11-13 ([scenarios-2026-11-13](scenarios-2026-11-13.md)). The demo proxy runs `caddy run --resume --config /etc/caddy/Caddyfile --adapter caddyfile` (`demo/compose.yaml`), and `--resume` starts Caddy from the last config it loaded, which is kept in its `/config` volume. With the fault on, `docker restart shop-proxy` leaves `/api/items` at 502 (checked by hand), so the restart would have failed Verify after the person approved it. The question: was this a gap in the prompt, or a fact the models never had?

## What the runs show

Raw data: one `misroute` run per model in each batch file; the model is named in the matching `.info.json`.

| Batch | Model | Plan | Tool calls |
|---|---|---|---|
| [`scenarios-20261116-105120`](data/scenarios-20261116-105120.jsonl) | `openai/gpt-oss-20b` | no action | inspect shop-proxy, inspect shop-web, knowledge_search |
| [`scenarios-20261116-114313`](data/scenarios-20261116-114313.jsonl) | `qwen/qwen3.8-27b` | no action | inspect shop-proxy, inspect shop-web, recent_events, knowledge_search ×5 |
| [`scenarios-20261116-120107`](data/scenarios-20261116-120107.jsonl) | `openai/gpt-6-luna` | no action | list_containers, inspect shop-proxy, inspect shop-web, recent_events |
| [`scenarios-20261116-121600`](data/scenarios-20261116-121600.jsonl) | `google/gemini-3.8-flash` | `docker.restart shop-proxy` | inspect shop-proxy, inspect shop-web, recent_events, knowledge_search ×4 |
| [`scenarios-20261116-124306`](data/scenarios-20261116-124306.jsonl) | `claude-haiku-5-5` | `docker.restart shop-proxy` | recent_events, inspect shop-proxy, inspect shop-web, knowledge_search ×2 |
| [`scenarios-20261116-125723`](data/scenarios-20261116-125723.jsonl) | `claude-sonnet-5-5` | `docker.restart shop-proxy` | inspect shop-proxy, inspect shop-web, recent_events |

All six diagnosed the cause: shop-proxy's loaded config forwards to port 8001, where nothing listens. All six inspected shop-proxy. The raw files keep the tool calls but not their results. The code shows what the inspect result held: `tools/docker_read._describe` printed image, state, health, restart policy, ports, mounts, labels and env, and `ContainerDetail` had no field for the start command. No model could read `--resume`.

The restart plans gave this reason:

- gemini-3.8-flash: "Restarting shop-proxy clears the misroute configuration loaded via its admin endpoint and restores the default configuration that routes traffic to shop-web on port 8000."
- Haiku: "The misroute came from a runtime admin API load of Caddyfile.misroute, which lives only in Caddy's memory, so restarting shop-proxy should return it to the Caddyfile built into its image". Its diagnosis cites the shop's docs: "repo docs deploy.md say shop-proxy's Caddyfile is built into its image."
- Sonnet: "The 502s come from a Caddy config pushed through the admin API, which lives only in memory. Restarting shop-proxy makes Caddy reload its original /etc/caddy/Caddyfile". Its diagnosis had no evidence entries.
- Sonnet, 2026-11-13 ([`scenarios-20261113-131246`](data/scenarios-20261113-131246.jsonl)): "The file on disk, /etc/caddy/Caddyfile, is the one that worked at startup, so restarting the proxy should drop the runtime config and load the original one."

The no-action plans gave this reason:

- gpt-oss-20b: "a mis‑configured upstream in the Caddyfile … This requires editing the Compose file or the Caddy configuration and recreating the shop‑proxy container". Its diagnosis calls it "the hard‑coded upstream in the baked Caddyfile". That is not what happened, since the bad config was loaded live.
- Qwen: "a simple restart would likely reload the same faulty configuration or fail to fix the upstream port mismatch."
- luna: "restarting shop-proxy would reload the same misroute rather than fix it." Nothing in its evidence shows how the proxy starts.

## Root cause

It is both a missing fact and a prompt gap:

1. **The fact was not available.** `inspect_container` did not show `Config.Entrypoint` or `Config.Cmd`, so the one fact that settles the question (`--resume`) never reached Investigate, Diagnose or Propose. The shop's docs ([`deploy.md`](../../demo/shop/docs/deploy.md)) say only that the Caddyfile is built into the image, and that is what Haiku leaned on.
2. **Propose had no rule for a config loaded at runtime.** Its only restart rule was "A restart that only brings the same fault back is not a fix." That rule cannot be applied without knowing what a restart loads. So each model answered from what it believed about Caddy. With a plain `caddy run --config …`, a restart does drop a config loaded through the admin API, which is why the restart looked right.

None of the six runs decided from evidence, so the three right answers are not evidence of a better reading. They are the same guess going the safe way.

## The fix

- `inspect_container` prints `start command (run again by every restart): […]`, the entrypoint and then the command as a JSON list (`ContainerDetail.command`, read from `Config.Entrypoint` and `Config.Cmd`). It is redacted as the health check's command is: the container's secret env values are taken out first, a word the base rules hide only after the word before it (`--password hunter`) is hidden whole, `redact_command` runs, and the line is cut at 300 characters. A secret passed to a flag the base rules do not know (`redis-server --requirepass …`) and not in the env still shows; the owner accepted that limit. The tool's description names it.
- Diagnose's evidence rule: when the cause is a config loaded or changed while the container ran (an admin API, a reload, a command run inside it), one entry gives the container's start command word for word as `inspect_container` showed it, with any mount that holds its config, or says no tool showed them. A secret in it is left out, as the rule just before it does for a setting's value. Propose reads only the diagnosis, so this is how the fact reaches the plan. The rule asks for the words themselves so that `--resume` reaches Propose. A summary such as "starts from the Caddyfile" could come from skimming `--config /etc/caddy/Caddyfile`, and Propose would then accept the restart.
- Propose's rule: a restart runs the start command again. For such a cause, a restart is planned only when the diagnosis's evidence shows the container starts from the right config: its start command, a config mount, or docs that say what it loads at start. The right file being in the image or on disk does not count, because some programs start from the last config they loaded. Otherwise Propose plans another enabled action that loads the right config, which keeps `misroute-mcp`'s `caddy_reload` open. If there is none, it answers no action and says which config to load again and how.

The rule is general, not about Caddy or misroute. It applies only when the cause is a config changed at runtime. A stopped container (`db-down`), a hung one (`hung`) and a wrong environment variable (`bad-env`) do not trigger it.

## Eval round

Run on this branch (commit `3a995f19`) on 2026-11-16, Bleep's backend stopped, judge `claude-code`. The owner limited the round to `misroute` ×3 and `db-down`, `bad-env` ×1 on luna and Haiku; `hung` and `misroute-mcp` were not run.

| Case | Model | Runs | Plan right | Before the fix (#616, 1 run) | Report |
|---|---|---|---|---|---|
| misroute | `openai/gpt-6-luna` | 3 | 2 of 3 (run 3: `docker.restart shop-proxy`) | 1 of 1 | [scenarios-2026-11-16-7](scenarios-2026-11-16-7.md) |
| misroute | `claude-haiku-5-5` | 3 | 3 of 3 | 0 of 1 | [scenarios-2026-11-16-8](scenarios-2026-11-16-8.md) |
| db-down, bad-env | `openai/gpt-6-luna` | 1 each | 2 of 2 exact, root cause 1.00 both | 2 of 2 | [scenarios-2026-11-16-9](scenarios-2026-11-16-9.md) |
| db-down, bad-env | `claude-haiku-5-5` | 1 each | 2 of 2 exact, root cause 1.00 both | 2 of 2 | [scenarios-2026-11-16-10](scenarios-2026-11-16-10.md) |

Every `misroute` run found the root cause (0.90) and put the start command, with `--resume`, in its evidence. So the fact now reaches Propose every time. What a model makes of it differs:

- **Haiku, 3 of 3 no action, each time because of `--resume`:** "A restart runs `caddy run --resume`, which can bring back the last config Caddy saved (the misroute one) instead of /etc/caddy/Caddyfile, so no enabled action is known to load the right config." In #616 it restarted.
- **luna, 2 of 3 no action.** Runs 1 and 2 said a restart "is not shown to load the corrected config". Run 3 quoted the same start command and read `--config` over `--resume`: "the proxy's start command loads `/etc/caddy/Caddyfile` … Restarting the proxy will discard the runtime misroute and reload its startup configuration." That is the risk named before the round: the new evidence can be misread toward the restart. luna's single right run in #616 was a guess (above), so 2 of 3 is not a measured drop from a known rate, but it is not a fix for luna either.

`db-down` (`docker.start shop-db`) and `bad-env` (no action, `DATABASE_URL` named) held on both models.

**What it shows:** the missing fact was the main cause for Haiku, and giving it the fact fixed the case. For luna the remaining miss is a reading of a flag it does not know well. One run per case elsewhere, so the `db-down`/`bad-env` result is a no-regression check, not a rate.

Spend: OpenRouter (luna) $9.4238 → $9.4446 used, +$0.021, matching the runs' own $0.0208; the backend was stopped, so nothing else used the key. Anthropic (Haiku) $0.042 by the runs' own costs (the console was not read). The judge ran on `claude-code`, $0. Raw data: [`scenarios-20261116-140340`](data/scenarios-20261116-140340.jsonl), [`-141053`](data/scenarios-20261116-141053.jsonl), [`-141757`](data/scenarios-20261116-141757.jsonl), [`-142045`](data/scenarios-20261116-142045.jsonl).

## Follow-up: a config file next to a resume flag (#645)

luna's third run above shows the fact reaching Propose and being misread. The start command names both `--resume` and `--config /etc/caddy/Caddyfile`, and Propose's rule counted "its start command" as evidence of what the container starts from, so luna took the `--config` file as that evidence. #645 added one sentence to the rule, after the one about a file in the image or on disk: a config file named in the start command does not show it either when the same command also has a flag or a state volume that may start the program from its last saved state (resume, restore, keep state), unless the evidence shows which of the two wins. It sits before "Otherwise…", so another enabled action that loads the right config stays open. It names no program and no flag. Diagnose was not changed: all three luna runs above already quoted the start command word for word.

Run on the #645 branch (commit `5177536a`) on 2026-11-16, Bleep's backend stopped, judge `claude-code`, luna only. #645 asked for `misroute` ×3 and `db-down` ×1.

| Case | Model | Runs | Plan right | Before #645 | Report |
|---|---|---|---|---|---|
| misroute | `openai/gpt-6-luna` | 3 | 3 of 3, root cause 1.00 each | 2 of 3 | [scenarios-2026-11-16-11](scenarios-2026-11-16-11.md) |
| db-down | `openai/gpt-6-luna` | 1 | exact (`docker.start shop-db`), root cause 1.00 | 1 of 1 | [scenarios-2026-11-16-12](scenarios-2026-11-16-12.md) |

Each `misroute` plan gave the resume flag as its reason:

- Run 1: "restarting may resume the saved config, so no enabled action is a reliable fix."
- Run 2: "Restarting shop-proxy may resume the misrouted configuration saved through Caddy's admin API; the evidence does not establish that its start command loads the intended file instead."
- Run 3: "shop-proxy starts with Caddy's `--resume` flag, and the evidence does not show that startup will prefer the documented route over the saved misroute."

**What it shows:** with the extra sentence, luna weighed `--resume` against `--config` in all three runs and did not restart. Three runs are not a rate: they show the misreading from the run before did not come back, not that it never will. `db-down` is one run, a no-regression check.

The full round after #645 ([six models, second round](models-2026-11-16-2.md)) ran `misroute` once on each of the six models at `b124ae95`: all six proposed no action, and luna, gemini, Haiku, Sonnet and Qwen gave `--resume` as the reason. gpt-oss-20b blamed the Caddyfile on the host.

Spend: OpenRouter $9.4446 → $9.4607 used, +$0.016; the runs' own costs add up to $0.018. The backend was stopped, so nothing else used the key. Judge on `claude-code`, $0. Raw data: [`scenarios-20261116-143733`](data/scenarios-20261116-143733.jsonl), [`-144431`](data/scenarios-20261116-144431.jsonl).

## Reproduce

From `backend/`, Bleep's backend stopped and the demo shop up:

```bash
export EVAL_JUDGE_MODEL=claude-code
# OpenRouter: LLM_PROVIDER=openrouter LLM_MODEL=openai/gpt-6-luna LLM_CHEAP_MODEL=openai/gpt-6-luna
# Anthropic: LLM_PROVIDER=anthropic LLM_MODEL=claude-haiku-5-5 LLM_CHEAP_MODEL=claude-haiku-5-5
uv run --group evals python -m evals.run --only misroute --repeat 3
uv run --group evals python -m evals.run --only db-down,bad-env --repeat 1
```
