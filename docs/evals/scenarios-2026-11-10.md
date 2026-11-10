# Scenario evals, 2026-11-10

> `openai/gpt-6-luna` diagnosed and proposed for 1 look on request, 3 runs each; Claude (Opus 5.5) graded the root causes by hand. The root cause passed in 0 of 3 runs and the plan was exact in 3 of 3.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | Claude (Opus 5.5), by hand, against each scenario's expected root cause; every grade and its note in [`scenarios-20261110-124747.grades.jsonl`](data/scenarios-20261110-124747.grades.jsonl) |
| Scenarios | look-healthy, 3 runs each |
| When | 2026-11-10 12:47 UTC, 3 min |
| Commit | `13a0cf2` |
| Raw data | [`scenarios-20261110-124747.jsonl`](data/scenarios-20261110-124747.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --judge manual --only look-healthy --repeat 3` (in `backend/`) |
| Graded | `uv run --group evals python -m evals.report ../docs/evals/data/scenarios-20261110-124747.jsonl --grades ../docs/evals/data/scenarios-20261110-124747.grades.jsonl --grader 'Claude (Opus 5.5)' --out ../docs/evals/scenarios-2026-11-10.md` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. A look on request switches no fault: once the watcher sees every container of the service healthy, the run asks for a look as a person's tap does, times count from then, and a look whose plan is no action goes on to Report, whose text is quoted. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app, and the linked repo docs `DannyKuk/bleep: demo/shop/docs/deploy.md`, `DannyKuk/bleep: demo/shop/docs/services.md`, read from this checkout.

## look-healthy

- Opened by a look on request
- Expected root cause: Nothing is wrong with the shop: every container runs, none is unhealthy, and nothing shows a failure.
- Expected category `unknown`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.30 fail | unknown | no action | yes | no | 10 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `inspect_container({"container": "shop-web"})`, `container_stats({"container": "shop-worker"})`, `recent_events({"minutes": 60})`, `knowledge_search({"query": "WORKER_BATCH_MB expected value or configuration setting", "sources":…)`, `container_logs({"container": "shop-worker", "tail": 100})`, `container_logs({"container": "shop-web", "tail": 100})`, `container_logs({"container": "shop-proxy", "tail": 100})`, `inspect_container({"container": "shop-proxy"})` | 56,127 | 7,961 | $0.0042 | 0 s | 33 s |
| 2 | 0.30 fail | unknown | no action | yes | no | 10 | `list_containers`, `inspect_container({"container": "shop-worker"})`, `inspect_container({"container": "shop-web"})`, `container_stats({"container": "shop-worker"})`, `recent_events({"minutes": 30})`, `knowledge_search({"query": "SHOP_DEBUG_HOOK setting", "sources": ["repo"]})`, `knowledge_search({"query": "WORKER_BATCH_MB setting", "sources": ["repo"]})`, `inspect_container({"container": "shop-proxy"})`, `container_logs({"container": "shop-web", "since_minutes": 1440, "tail": 1000})`, `container_logs({"container": "shop-proxy", "since_minutes": 1440, "tail": 1000})` | 44,733 | 8,083 | $0.0041 | 0 s | 33 s |
| 3 | 0.40 fail | unknown | no action | yes | no | 10 | `list_containers`, `container_stats({"container": "shop-worker"})`, `container_stats({"container": "shop-web"})`, `container_stats({"container": "shop-db"})`, `container_stats({"container": "shop-proxy"})`, `recent_events({"minutes": 30})`, `inspect_container({"container": "shop-worker"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-proxy", "since_minutes": 60, "tail": 200})`, `container_logs({"container": "shop-web", "since_minutes": 60, "tail": 200})` | 38,817 | 6,837 | $0.0032 | 0 s | 31 s |

- Run 1 diagnosis: The collected evidence does not establish a failing component or a cause for the shop incident; all four shop containers are running and shop-web is healthy. The specific dashboard request symptom and its proxy response are unavailable.
  - Judge: says no failing component was found, but treats the look as a reported dashboard failure it could not see; never says nothing is wrong
  - Report (No action proposed, nothing was done): A dashboard issue was reported for demo/compose:shop. The investigation found all four containers running and shop-web healthy, but the dashboard symptom and proxy response were unavailable. No action was taken, and the issue remains unconfirmed. Cause: The root cause was not found; available evidence did not identify a failing component. The specific dashboard request symptom and its proxy response were missing.
- Run 2 diagnosis: The collected evidence does not establish a failure or its cause: shop-web returned successful health and items requests, and the other services are running. The dashboard's reported failing behavior has not been reproduced or captured.
  - Judge: finds every container healthy, then calls it a dashboard failure that was not reproduced; never says nothing is wrong
  - Report (No action proposed, nothing was done): The dashboard failure on demo/compose:shop was not reproduced. The available checks showed shop-web answering health and items requests successfully, with the proxy, database, and worker running. No action was proposed or taken, and nobody confirmed whether the dashboard works; the next step is to capture the failing URL, time, and browser error or response when reproducing it. Cause: The cause was not found: the collected evidence does not establish a failure, and the reported dashboard behavior was not captured.
- Run 3 diagnosis: The collected evidence does not establish a failure or its cause; shop-web is healthy and is returning successful health and item requests, while shop-proxy reports serving but no user-facing request failure is shown. The reported incident cannot be diagnosed from these results.
  - Judge: no failure shown, but 'the reported incident cannot be diagnosed': a failure assumed, not a healthy service
  - Report (No action proposed, nothing was done): The reported issue on demo/compose:shop was investigated, but the available evidence did not show a user-facing failure. No action was taken, and the incident remains unresolved because nobody confirmed whether the service works from the user's perspective. Cause: The cause was not found: shop-web and shop-db were healthy, and the collected logs showed successful requests without a demonstrated failure.

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| look-healthy | 3 | 0 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 10.0 | 46,559 | 8,083 | $0.0115 | 32 s |
| **All** | 3 | 0 of 3 | 3 of 3 | 3 of 3 | 0 of 3 | 0 | 0 | 0 | 10.0 | 46,559 | 8,083 | $0.0115 | 32 s |

## Cost

- Model under test: $0.0115.
- Judge: none, graded by hand.

## What it shows

- The root cause passed in 0 of 3 runs, and the category was right in 3 of 3.
- The plan was exactly the expected one in 3 of 3 runs; 0 proposed an unnecessary action.
- The largest single prompt was 8,083 tokens (look-healthy, run 2).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
