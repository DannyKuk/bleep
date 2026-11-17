# Scenario evals, 2026-11-17

> `openai/gpt-6-luna` diagnosed and proposed for 1 scripted host, 3 runs each; `claude-code` judged the root causes. The root cause passed in 3 of 3 runs and the plan was exact in 3 of 3.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | `claude-code` through DeepEval GEval; a root cause passes at 0.7 or more |
| Scenarios | coolify-set-env, 3 runs each |
| When | 2026-11-17 08:27 UTC, 2 min |
| Commit | `0d2571eb` |
| Raw data | [`scenarios-20261117-082723.jsonl`](../../data/evals/scenarios-20261117-082723.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --judge claude-code --only coolify-set-env --repeat 3` (in `backend/`) |

Each run opens the incident from the scenario's scripted signals and runs the graph to the end of Propose. Nothing is executed. Times count from the incident opening; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. A scripted host switches no fault and touches no Docker engine: the run opens the incident from the host's scripted signals, and times count from then. Knowledge search was offered over the shipped Docker docs snapshot, as in the app.

## coolify-set-env

- Opened by the signals of a scripted host
- Expected root cause: storefront-web exits at startup and keeps being restarted because the setting SESSION_TTL_S, which its latest deployment (the session handling moved into its own module) requires, is not set in the application's environment in Coolify.
- Expected category `config`, plan `coolify.set_env storefront with params {"key": "SESSION_TTL_S"}`, `coolify.redeploy storefront` in this order, never `docker.restart`, `docker.start`, `docker.stop`
- Must name `SESSION_TTL_S`, in the diagnosis and in the plan

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.90 pass | config | coolify.set_env storefront, coolify.redeploy storefront | yes | no | 9 | `list_containers`, `coolify_applications`, `knowledge_search({"query": "SESSION_TTL_S", "sources": ["repo"]})`, `inspect_container({"container": "storefront-web"})`, `coolify_deployments({"app": "store0uuid0000000000000a1"})`, `coolify_env_keys({"app": "store0uuid0000000000000a1"})`, `recent_events({"minutes": 60})`, `container_logs({"container": "storefront-web", "since_minutes": 60, "tail": 80})`, `knowledge_search({"query": "SESSION_TTL_S", "sources": ["repo"]})` | 30,689 | 4,804 | $0.0026 | 0 s | 21 s |
| 2 | 0.90 pass | config | coolify.set_env storefront, coolify.redeploy storefront | yes | no | 8 | `list_containers`, `inspect_container({"container": "storefront-web"})`, `coolify_applications`, `recent_events({"minutes": 30})`, `coolify_env_keys({"app": "store0uuid0000000000000a1"})`, `knowledge_search({"query": "What is the documented value or required setting for SESSION_TTL_S?"…)`, `coolify_deployments({"app": "store0uuid0000000000000a1"})`, `knowledge_search({"query": "SESSION_TTL_S", "sources": ["repo"]})` | 21,801 | 4,403 | $0.0018 | 0 s | 26 s |
| 3 | 0.90 pass | config | coolify.set_env storefront, coolify.redeploy storefront | yes | no | 8 | `list_containers`, `inspect_container({"container": "storefront-web"})`, `container_logs({"container": "storefront-web", "tail": 60})`, `recent_events({"minutes": 30})`, `coolify_applications`, `knowledge_search({"query": "SESSION_TTL_S", "sources": ["repo"]})`, `coolify_deployments({"app": "store0uuid0000000000000a1"})`, `coolify_env_keys({"app": "store0uuid0000000000000a1"})` | 21,157 | 4,095 | $0.0016 | 0 s | 20 s |

- Run 1 diagnosis: storefront-web is aborting startup because SESSION_TTL_S is missing. The startup log identifies 3600 seconds as the default for a new install, but the repository docs search returned no documented value, so the remedy value is not confirmed.
  - Judge: The diagnosis gets the root cause right. It names storefront-web as the failing component and says it fails because SESSION_TTL_S is missing from its environment, which makes startup abort. Container logs and the inspected environment (DATABASE_URL, NODE_ENV and PORT are set, SESSION_TTL_S is not) back this up, and the config category fits. It does not mention that the latest deployment, which moved session handling into its own module, is what introduced the requirement. It also does not mention the restart loop or that the variable is managed in Coolify. These are minor gaps in context and do not change the cause. The added detail about the 3600-second default and the unconfirmed remedy value is accurate and does not detract.
  - Names: the diagnosis names every one, the plan names every one
- Run 2 diagnosis: storefront-web is missing the SESSION_TTL_S environment variable, so storefront 3.4.0 aborts startup when its session module requires a duration. The log gives 3600 seconds as the default for a new install; the latest deployment, commit 'Move session handling into its own module', is consistent with the new requirement.
  - Judge: The diagnosis matches the expected root cause. It names the same failing component (storefront-web) and the same reason it fails: the SESSION_TTL_S environment variable is not set, so storefront 3.4.0 aborts at startup. It also links the failure to the latest deployment, 'Move session handling into its own module', which matches the expected note that the session module refactor added this requirement. The evidence supports the conclusion: the logs show startup aborting because SESSION_TTL_S is unset, and the container environment lists only DATABASE_URL, NODE_ENV and PORT. The category is correctly given as config, and the extra detail about the 3600-second default is correct. Two small gaps: it never says the container keeps being restarted, and it doesn't say outright that the variable is missing from the application's Coolify environment, though its evidence from coolify_deployments and inspect_container implies this.
  - Names: the diagnosis names every one, the plan names every one
- Run 3 diagnosis: storefront-web is restarting because the SESSION_TTL_S environment variable is unset, and the session module aborts startup without a seconds value. The service log gives 3600 seconds as the default for a new install.
  - Judge: The diagnosis matches the expected root cause. It names the same failing component, storefront-web, which keeps restarting, and the same reason: SESSION_TTL_S is missing from the application's environment, so startup aborts. Its evidence backs this up: the container logs show startup aborting because SESSION_TTL_S is unset, and inspecting the container shows only DATABASE_URL, NODE_ENV and PORT in the environment. It correctly classifies the fault as config. It does not mention that the latest deployment, which moved session handling into its own module, introduced the requirement, or that the environment is managed in Coolify. Those omissions make it slightly less complete but do not change the cause. The note about the 3600-second default is correct extra detail and is not penalised.
  - Names: the diagnosis names every one, the plan names every one

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| coolify-set-env | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 8.3 | 24,549 | 4,804 | $0.0060 | 22 s |
| **All** | 3 | 3 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 8.3 | 24,549 | 4,804 | $0.0060 | 22 s |

## Cost

- Model under test: $0.0060.
- Judge: n/a (billed to a plan, not per call).

## What it shows

- The root cause passed in 3 of 3 runs, and the category was right in 3 of 3.
- The plan was exactly the expected one in 3 of 3 runs; 0 proposed an unnecessary action.
- The diagnosis named everything its fix needs in 3 of 3 runs that score it, and the plan in 3 of 3.
- The largest single prompt was 4,804 tokens (coolify-set-env, run 1).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
