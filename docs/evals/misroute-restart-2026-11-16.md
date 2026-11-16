# Restarting a proxy whose bad config survives a restart, 2026-11-16

> **Answer:** a missing fact and a prompt gap together. `inspect_container` never showed a container's start command, so none of the six models in the #616 round could see that shop-proxy runs `caddy run --resume`, which starts from the last config Caddy loaded. Every run inspected shop-proxy. The three that proposed `docker.restart shop-proxy` (gemini-3.8-flash, Claude Haiku 5.5, Claude Sonnet 5.5) used Caddy's default behaviour: they said a config loaded through the admin API "lives only in memory" and that a restart reloads the Caddyfile. The three that proposed no action were guessing too, just the other way: gpt-oss-20b blamed a "baked Caddyfile", Qwen said a restart "would likely reload the same faulty configuration", and luna said it "would reload the same misroute" without anything it had read showing that. Propose also had no rule about a config loaded while the container runs. #642 adds the start command to `inspect_container`. It also asks Diagnose to quote such a container's start command in its evidence, and tells Propose that a restart fixes a config loaded at runtime only when the evidence shows the container starts from the right one. Whether this moves the three models is measured in the eval round below.

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

Pending: the main session runs this round on the branch and fills in this section. The start command is new evidence, and it could push a run the wrong way: a model that reads `--config /etc/caddy/Caddyfile` and misses `--resume` could now argue for the restart. luna is right today without the fact, so it is in the round. `hung` is the only scenario that expects a `docker.restart`, and `misroute-mcp` checks the "another enabled action" clause. The plan:

| Case | Models | Runs | What it checks |
|---|---|---|---|
| misroute | `openai/gpt-6-luna`, `claude-haiku-5-5` | 3 each | no action, no restart; whether the diagnosis names `--resume` from `inspect_container` |
| db-down | `openai/gpt-6-luna`, `claude-haiku-5-5` | 1 each | still `docker.start shop-db` |
| bad-env | `openai/gpt-6-luna`, `claude-haiku-5-5` | 1 each | still no action with the `DATABASE_URL` change |
| hung | `openai/gpt-6-luna` | 1 | still `docker.restart shop-web` |
| misroute-mcp | `openai/gpt-6-luna` | 1 | still one `mcp.call` of `caddy_reload` on shop-proxy |

From `backend/`, Bleep's backend stopped and the demo shop up:

```bash
export EVAL_JUDGE_MODEL=claude-code
# OpenRouter: LLM_PROVIDER=openrouter LLM_MODEL=openai/gpt-6-luna LLM_CHEAP_MODEL=openai/gpt-6-luna
# Anthropic: LLM_PROVIDER=anthropic LLM_MODEL=claude-haiku-5-5 LLM_CHEAP_MODEL=claude-haiku-5-5
uv run --group evals python -m evals.run --only misroute --repeat 3
uv run --group evals python -m evals.run --only db-down,bad-env --repeat 1
# luna only
uv run --group evals python -m evals.run --only hung,misroute-mcp --repeat 1
```

Results: not run yet.
