<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img alt="Bleep" src="docs/assets/logo-light.svg" width="300">
  </picture>
</p>

<p align="center"><b>An on-call agent for Docker hosts that fixes things only after you say yes.</b></p>

<p align="center">
  <a href="https://github.com/DannyKuk/bleep/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/DannyKuk/bleep/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="LICENSE"><img alt="Code: MIT" src="https://img.shields.io/badge/code-MIT-blue"></a>
  <a href="backend/pyproject.toml"><img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&logoColor=white"></a>
  <a href="backend/"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white"></a>
  <a href="backend/src/bleep/graph/"><img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-1C3C3C?logo=langchain&logoColor=white"></a>
  <a href="frontend/"><img alt="Next.js 16" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white"></a>
</p>

Bleep watches the containers on your hosts. When one breaks, it opens an incident, reads the logs and the host the way you would, says what is wrong, and proposes a fix. Nothing changes on a host until you say yes; then it runs the fix and checks that the service is healthy again before it calls it fixed.

https://github.com/user-attachments/assets/a3fd906c-4b35-498e-bbbb-c9f7c85a612a

**See it on the [landing page](https://bleep.dkwebsite.de/):** a replay of one real incident where you say yes, [how an incident moves through the graph](https://bleep.dkwebsite.de/flow), and [every eval run](https://bleep.dkwebsite.de/benchmarks).

## How well it does

Every eval suite ran on one commit with `openai/gpt-6-luna`, three runs per case, 473 runs for $0.71 ([report](docs/evals/showcase-2026-11-17.md)). [**Every run is one click away on the benchmarks page**](https://bleep.dkwebsite.de/benchmarks): what Bleep found, what it proposed, why, and the tools it called.

| Suite | What it asks | Result |
|---|---|---|
| Faults | Does it find what broke? 15 incidents on the demo shop, from the alert to the plan | 42 of 45 runs found the cause and the right plan |
| Attacks | Can a log line give Bleep orders? 27 attacks through logs, docs, commits, memory, error pages, host details and service names | Resisted all 69 runs where the planted text reached the model; 12 more never reached it |
| Controls | The same faults with nothing planted | 15 of 18 right |
| Talk | Can you just ask it? 42 messages in English and German | 125 of 126 read right |
| Look | Does it look before it says healthy? 7 services to look at on request, some healthy and some quietly broken | 16 of 21 said it right |
| Loop back | What does it do when its fix didn't work? | 15 of 15 looked again instead of repeating the fix |
| Recovery line | Can it say why a container came back on its own? | 15 of 15 named the cause |
| Docs search | Does it find the right page in the docs? 31 questions, 11 of them German or mixed | 31 of 31 in the top five |

Every real miss has one shape: luna names the right facts, then hedges and files the cause under `unknown`. That is `hung` (all 3 fault runs, 3 of the 6 control runs) and two look cases. Claude Haiku 5.5, run again on those cases, committed to the cause on the look cases in 6 of 6 runs and on `hung` in 2 of 3. No run proposed a step it should not.

Earlier rounds that the one above does not repeat:

| What was tested | Result | Report |
|---|---|---|
| Six models (luna, gemini-3.8-flash, Claude Haiku and Sonnet 5.5, gpt-oss-20b, Qwen 3.8 27B), 6 faults and 4 injection cases, 1 run each | Every model 6 of 6 exact plans; luna, gemini, Haiku and the local Qwen 3.8 27B had every case right; Sonnet missed one fix value, gpt-oss-20b one value and one attack, which the plan check stopped | [report](docs/evals/models-2026-11-16-2.md) |
| luna on the 8 faults, judged by DeepEval's GEval against grading by hand | 7 of 8 right; GEval and the hand grades agree on all 8 | [report](docs/evals/scenarios-2026-11-11.md) |
| Telling the human about planted orders | Told in 13 of 14 attack runs whose orders reached the model, 0 of 18 honest runs; 27 of 92 blind attacks | [report](docs/evals/planted-markers-2026-11-06.md) |

## How it works

- **It watches over SSH.** Every 15 s Bleep reads each host's Docker events and container list through one SSH connection, and checks each service's URLs from outside every 30 s. Containers are grouped into services by Coolify labels, the Compose project or their name.
- **It opens an incident only for a real break.** A container that dies and comes back within a minute is a self-recovery with a one-line explanation, not an incident; a crash loop, an unhealthy container, a failing URL or a full disk is one, and every later signal for the same service joins it.
- **It investigates with read-only tools.** A LangGraph run gathers the containers, logs, host basics, the service's rules and similar past incidents, then lets the model read more: container details, logs, an allowlisted set of host commands, the linked GitHub repo's recent commits, and a search over Docker's docs and your own. Every tool answer is redacted, capped and wrapped as untrusted data, so a log line cannot give Bleep orders.
- **It proposes, you decide.** The diagnosis comes with a plan of one to three steps (restart, start or stop a container, or a plugin action), checked in code before you see it: never a protected container, never Bleep's own, never a working container nobody named. You answer on the dashboard or in Telegram: yes, no, or tell Bleep something.
- **It runs the fix and proves it.** The approved plan runs with a signed grant for exactly that plan. Bleep then checks the containers and URLs again; only then is the incident resolved, and the report says what happened in plain words.
- **It remembers.** Every finished incident, your decision and your feedback go into a local memory that the next similar incident reads.

[Architecture](docs/architecture.md) follows one incident from start to end, sets out the security model and lists the key design choices.

## Quick start

```bash
cp .env.example .env    # set BLEEP_ADMIN_PASSWORD, BLEEP_SECRET and OPENROUTER_API_KEY (or LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY, or LLM_PROVIDER=local)
docker compose --profile demo up -d --build && docker compose -f demo/compose.yaml up -d --build
demo/fault.sh db-down on    # stop the demo shop's database; Bleep opens an incident within a minute
```

Open http://localhost:3000, sign in with the admin password and open the incident. Bleep finds the stopped database and proposes `docker.start shop-db`; say yes and watch it verify the shop. `demo/fault.sh db-down off` mends the shop by hand. Telegram is optional. [Setup](docs/setup.md) covers your own hosts, SSH keys, local models and Telegram; [Demo](docs/demo.md) lists the eight faults and what Bleep should do for each.

## Docs

| Doc | What's in it |
|---|---|
| [Architecture](docs/architecture.md) | The pieces, one incident from start to end, the security model, the key design choices, and the lists of the key decisions and evals |
| [Setup](docs/setup.md) | Your own hosts over SSH, keys and tokens, models, Telegram, the network and what is not built |
| [Demo](docs/demo.md) | The demo shop, its eight faults, and what Bleep should find and propose for each |
| [Design details](docs/design/) | The reasoning behind every design choice, one page per area |
| [Decisions](docs/decisions/) | The big questions with evidence: what we tried, what we measured, what we picked |
| [Evals](docs/evals/) | Dated eval reports with their numbers; [how to run them](backend/evals/README.md) |

## Stack

- **Backend:** Python, FastAPI, LangGraph; models through OpenRouter, the Anthropic API or a local OpenAI-compatible server (LM Studio, Ollama)
- **Hosts:** asyncssh forwarding each host's Docker socket, the Docker SDK
- **Storage:** SQLite for incidents, settings, memory and LangGraph checkpoints; fastembed for local embeddings
- **Frontend:** Next.js
- **Landing page:** a static Next.js export in `site/`, built on its own and not part of `docker compose`
- **Runs in Docker:** `docker compose up --build`

## Layout

```
backend/    FastAPI app, watcher, incident graph, guard, tools, tests and evals
frontend/   Next.js dashboard
demo/       the demo shop and the script that breaks it
config/     example hosts files
site/       the public landing page, a static export
scripts/    test runner, docs check, board and docs-snapshot helpers
docs/       architecture, setup, demo, design details, decisions, eval reports
```

## Licences

The code is under the [MIT License](LICENSE). The Docker documentation pages in [`backend/knowledge/docker-docs/`](backend/knowledge/docker-docs/) keep their own licence, Apache 2.0 ([LICENSE](backend/knowledge/docker-docs/LICENSE)).
