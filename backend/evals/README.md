# Evals

Real faults on the demo stack, a real model, scored. Each run switches one fault on with `demo/fault.sh`, lets Bleep's own watcher open the incident, runs the incident graph to the end of Propose and stops there (nothing is ever executed), switches the fault off, and scores what Bleep found:

- **Root cause**: a judge model (DeepEval GEval) scores 0 to 1 how well the diagnosis names the scenario's expected root cause; 0.7 or more passes. The judge is never the model under test.
- **Category**: the diagnosis's category, exactly.
- **Plan**: exact match on action and target; an **unnecessary action** is a step beyond the expected plan, or one the scenario forbids.
- **Cost of getting there**: tool calls, tokens, cost, the largest single prompt, and the seconds from the fault to the incident and to the diagnosis.
- **Failed runs**: a run that ends early says how in `failure`: `timeout`, `context_length` (a model call reached its length limit before it answered) or `error`. The report counts them, out-of-context runs apart.

How it works and why is in [docs/design/evals.md](../../docs/design/evals.md). This is not part of `pytest`; the harness's own logic is tested in `tests/evals/` on fakes.

## Before a run

1. Start the demo shop from the repo root: `docker compose -f demo/compose.yaml up -d --build`. It is its own Compose project, `shop`, so its incidents open on the service `demo/compose:shop` ([demo](../../docs/demo.md)). The harness does not need Bleep's own containers.
2. If Bleep's backend runs (`docker compose --profile demo up`), stop it, or it sees the same faults and pages you: `docker compose stop backend`. The harness warns when it is running. Start it again afterwards with `docker compose --profile demo up -d`.
3. Point Bleep's model settings at the model under test. For a local model:

   ```bash
   export LLM_PROVIDER=local LOCAL_BASE_URL=http://localhost:1235/v1 LOCAL_MODEL=huihui-qwen3.8-27b-abliterated
   ```

   The harness runs on this machine, not in a container, so a `host.docker.internal` URL from `.env` is read as `localhost`. The same goes for the hosts file's URLs: the backend checks the shop at `http://host.docker.internal:8080`, shop-proxy's published port, and the harness at `http://localhost:8080`. A URL on a bare container name (`http://shop-web:8000`) does not answer from here; the harness logs it.

4. Make the judge reachable: `OPENROUTER_API_KEY` in `.env` for the default judge, or `export EVAL_JUDGE_MODEL=claude-code` to judge with the Claude Code CLI on your own plan.

`demo/fault.sh` switches the faults. It runs compose on `demo/compose.yaml` itself and acts on the engine `docker` points at; for a socket host the harness sets its `DOCKER_HOST` to the engine it watches (`EVAL_DOCKER_ENDPOINT`, below), so both act on the same one. For an SSH host the script keeps your shell's `DOCKER_HOST`, which must point at that host's engine ([on another host](../../docs/demo.md#on-another-host)).

## Run

From `backend/`:

```bash
uv run --group evals python -m evals.run --repeat 3        # every scenario, three runs each
uv run --group evals python -m evals.run --only db-down    # one scenario
uv run --group evals python -m evals.run --model other-model
uv run --group evals python -m evals.run --append data/evals/scenarios-20261103-104336.jsonl
```

`--append` continues an earlier batch: its runs go into that batch's raw file, numbered after the runs already there, and the report covers all of them. A suite can so run one repeat at a time, with the demo shop and the model server free for something else in between, and still end in one report. Each batch writes a report; the last one has every run. The harness refuses a raw file of another model under test.

`--model` replaces the model for the provider in use (`LOCAL_MODEL` for `local`, `LLM_MODEL` for `openrouter`). Each run takes up to its scenario's `timeout_s`, and the harness waits `EVAL_SETTLE_S` between runs.

## Settings

Read from the environment, then the repo's `.env`.

| Variable | Default | What it sets |
|---|---|---|
| `LLM_PROVIDER`, `LLM_MODEL`, `LOCAL_BASE_URL`, `LOCAL_MODEL` | Bleep's defaults | The model under test, as for Bleep itself. |
| `EVAL_JUDGE_MODEL` | `openai/gpt-6-luna` | The judge: an OpenRouter model id (needs `OPENROUTER_API_KEY`), or `claude-code` / `claude-code:<model>` (needs the `claude` CLI). |
| `EVAL_DOCKER_ENDPOINT` | `DOCKER_HOST`, else Docker Desktop's socket, else `unix:///var/run/docker.sock` | The demo host's Docker engine as this machine reaches it; also the fault script's `DOCKER_HOST` for a socket host. |
| `EVAL_HOSTS_FILE` | `config/hosts.demo.yaml` | The hosts file the demo host's rules and URLs come from. |
| `EVAL_HOST_ID` | `demo` | The host in that file. |
| `EVAL_FAULT_SCRIPT` | `demo/fault.sh` | The fault switch, called as `<script> <fault> on\|off` from the repo root; exit 0 is switched, any other exit stops the run (`fault.sh` exits 2 on a fault it does not know). |
| `EVAL_SETTLE_S` | `30` | Seconds between runs, for the stack to come back. |
| `EVAL_WATCH_INTERVAL_S` | `15` | Seconds between watcher ticks, Bleep's own default. |

## A model that runs out of context

A reasoning model can spend its whole context thinking. On the local Qwen with a 16,128-token context, one live Diagnose call reasoned for 9,729 tokens on a 6,361-token prompt and ended without an answer (`LengthFinishReasonError`, [demo](../../docs/demo.md#full-runs)). The harness records such a run as failed with `failure: context_length`, keeps what Investigate found, switches the fault off and goes on with the next run; the report counts these runs in its headline, in the Totals' "Out of context" column and under "What it shows".

Bleep's settings cannot cap the model's output tokens: `ModelSettings` has no such field and `llm/factory.py` sends no `max_tokens`, so the harness cannot either. Until they do, give the model room in the model server instead: load it with a larger context (LM Studio's context length), so that the largest prompt (the report's peak prompt) plus the model's reasoning fit.

## What a run writes

- `data/evals/scenarios-<YYYYMMDD-HHMMSS>.jsonl`: one line per run, written as the run finishes, with the diagnosis, the plan and every score (`ScenarioResult` in `scenarios.py`).
- `docs/evals/scenarios-YYYY-MM-DD.md`: the report, with the setup, a table per scenario, the totals, the cost and what the numbers show. A second report on the same day gets `-2`, `-3`, ... Read every failed diagnosis by hand before you trust a pass rate, and note judge mistakes in the report.

## A scenario

One file per fault in `scenarios/`, named after its `id`:

```yaml
id: db-down
fault: db-down                    # the name demo/fault.sh switches
subject:                          # the incident the fault must open
  kind: service
  host_id: demo
  service_id: demo/compose:shop
expected:
  root_cause: >-                  # what the judge compares the diagnosis with
    The database container shop-db is stopped, so shop-web cannot connect to its database and
    the shop's API fails.
  category: dependency            # config, resource, dependency, code, network, host or unknown
  action:
    kind: act                     # or no_action, with no steps
    steps:                        # one to three, exact container names
      - action: docker.start
        target: shop-db
    ordered: false                # true when the step order is part of the answer
  must_not: [docker.restart, docker.stop]
timeout_s: 900                    # from the fault on to the end of Propose
```

`tests/evals/test_scenarios_load.py` loads every file here, so a malformed scenario fails the tests.
