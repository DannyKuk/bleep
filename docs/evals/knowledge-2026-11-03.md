# Knowledge search retrieval, 2026-11-03

> **Answer:** for 20 questions written by hand, each with the snapshot page that answers it, knowledge search put the right page in its top 5 for **20 of 20 (hit@5 100%)** and first for **20 of 20 (hit@1 100%)**, with Bleep's settings (local bge-small, chunks of at most 300 estimated tokens with 50 of overlap). Embedding the 27-page snapshot (444 chunks) took 19.8 s on this laptop. One search took 6 ms (median) once the model was loaded.

## Setup

- **Corpus**: the Docker docs snapshot in [`backend/knowledge/docker-docs/`](../../backend/knowledge/docker-docs/), 27 pages from docker/docs at commit `51657f2`, fetched 2026-11-03.
- **Questions**: [`backend/evals/knowledge/questions.yaml`](../../backend/evals/knowledge/questions.yaml), 20 questions as Investigate would ask them, each naming one expected page. They were written by hand from the pages, in other words than the pages use.
- **Runner**: [`backend/evals/knowledge.py`](../../backend/evals/knowledge.py). It ingests the snapshot into a temporary database with the real local embedder (`fastembed`, `BAAI/bge-small-en-v1.5`, two threads), then runs `KnowledgeIndex.search(question, k=5, sources={"docker"})` for each question. A question's rank is the expected page's position among the 5 pages returned (one hit per page). No chat model is called.
- **Machine**: Apple M4 Pro, macOS, outside Docker.

## Result

With Bleep's settings (300 tokens, overlap 50):

| Chunk limit | Overlap | Pages | Chunks | Ingest | hit@1 | hit@3 | hit@5 | MRR@5 | Search (median) |
|---|---|---|---|---|---|---|---|---|---|
| 300 | 50 | 27 | 444 | 19.8 s | 100% | 100% | 100% | 1.00 | 6 ms |

| # | Question | Expected page | Rank | Top 5 |
|---|---|---|---|---|
| 1 | A container with the unless-stopped restart policy did not come back after the Docker daemon restarted. Why not? | `restart-policies` | 1 | restart-policies, compose-services, compose-deploy, daemon-logs, live-restore |
| 2 | Why is a container that crashes right after it starts not being restarted by its on-failure policy? | `restart-policies` | 1 | restart-policies, compose-deploy, compose-services, daemon-logs, daemon-troubleshoot |
| 3 | The kernel killed a container's process because the host ran out of memory. How do memory limits and the OOM killer work for containers? | `resource-constraints` | 1 | resource-constraints, daemon-troubleshoot, running-containers, compose-services, daemon-logs |
| 4 | What does it mean when docker run itself fails with exit code 125? | `running-containers` | 1 | running-containers, daemon-logs, restart-policies, daemon-troubleshoot, bind-mounts |
| 5 | A container exited with code 127. What does that exit status say? | `running-containers` | 1 | running-containers, restart-policies, cli-events, daemon-logs, dockerfile-healthcheck |
| 6 | How do I make the web service wait in Compose until its database is healthy before it starts? | `compose-startup-order` | 1 | compose-startup-order, compose-services, compose-deploy, cli-events, compose-networking |
| 7 | What do interval, timeout, start period and retries mean for a container health check? | `dockerfile-healthcheck` | 1 | dockerfile-healthcheck, running-containers, compose-services, compose-deploy, compose-startup-order |
| 8 | Container logs are filling the disk. How do I rotate json-file logs by size and number of files? | `logging-json-file` | 1 | logging-json-file, logging-dual, logging-configure, cli-logs, running-containers |
| 9 | docker logs shows nothing for a container that sends its logs to a remote logging driver. Why? | `logging-dual` | 1 | logging-dual, logging-configure, daemon-logs, cli-logs, logging-local |
| 10 | Where can I read the Docker daemon's own logs on a Linux host that runs systemd? | `daemon-logs` | 1 | daemon-logs, daemon-troubleshoot, logging-dual, cli-logs, cli-events |
| 11 | Which events does Docker emit when a container dies, is OOM killed or changes its health status? | `cli-events` | 1 | cli-events, compose-services, resource-constraints, live-restore, running-containers |
| 12 | How much disk space do images, containers, local volumes and the build cache take up? | `cli-system-df` | 1 | cli-system-df, live-restore, running-containers, pruning, resource-constraints |
| 13 | How do I free disk space by removing unused images, stopped containers and build cache? | `pruning` | 1 | pruning, volumes, cli-system-df, running-containers, logging-dual |
| 14 | The database lost its data after its container was recreated. How does a named volume keep data? | `volumes` | 1 | volumes, compose-services, cli-events, pruning, tmpfs |
| 15 | How do I mount a temporary in-memory filesystem into a container? | `tmpfs` | 1 | tmpfs, volumes, compose-services, bind-mounts, daemon-troubleshoot |
| 16 | Can containers keep running while the Docker daemon is down or being upgraded? | `live-restore` | 1 | live-restore, restart-policies, cli-events, running-containers, daemon-logs |
| 17 | Services in the same Compose project cannot reach each other by service name. How does Compose networking work? | `compose-networking` | 1 | compose-networking, compose-services, compose-startup-order, compose-deploy, network-bridge |
| 18 | A published port is not reachable from other machines. Which host addresses does port publishing bind to? | `port-publishing` | 1 | port-publishing, networking, network-bridge, compose-services, daemon-troubleshoot |
| 19 | How long does Compose wait after sending SIGTERM before it kills a service's container on stop? | `compose-services` | 1 | compose-services, compose-startup-order, compose-deploy, dockerfile-healthcheck, compose-networking |
| 20 | docker stats shows memory usage that includes the page cache. How are a container's memory metrics computed from cgroups? | `runtime-metrics` | 1 | runtime-metrics, running-containers, cli-system-df, resource-constraints, daemon-troubleshoot |

The same questions at other chunk sizes, to choose the limit ([knowledge chunks](../decisions/knowledge-chunks.md)):

| Chunk limit | Overlap | Chunks | Ingest | hit@1 | hit@3 | hit@5 | MRR@5 | Largest chunk (model tokens) | Missed first place |
|---|---|---|---|---|---|---|---|---|---|
| 200 | 50 | 595 | 17.4 s | 100% | 100% | 100% | 1.00 | | |
| 200 | 0 | 556 | 15.9 s | 100% | 100% | 100% | 1.00 | | |
| **300** | **50** | **444** | **19.8 s** | **100%** | **100%** | **100%** | **1.00** | **398** | |
| 400 | 50 | 378 | 22.4 s | 90% | 100% | 100% | 0.95 | 518 | #2 and #3, both second |
| 400 | 0 | 374 | 21.6 s | 95% | 100% | 100% | 0.97 | | #2, second |
| 600 | 50 | 341 | 22.1 s | 90% | 100% | 100% | 0.95 | | #2 and #3, both second |

The largest chunk was counted with the model's own tokenizer, headings included, at 300 and 400 tokens only; the model reads at most 512 and cuts the rest. The 300-token run was made twice, with the same numbers (19.8 s, 100% at every cut-off).

Raw data, one JSON line per question after a totals line: [`data/knowledge-20261103-203538-chunks-300.jsonl`](data/knowledge-20261103-203538-chunks-300.jsonl) and its repeat [`data/knowledge-20261103-203626.jsonl`](data/knowledge-20261103-203626.jsonl) (Bleep's settings), [`-chunks-200`](data/knowledge-20261103-203421-chunks-200.jsonl), [`-chunks-200-overlap-0`](data/knowledge-20261103-203555-chunks-200-overlap-0.jsonl), [`-chunks-400`](data/knowledge-20261103-203357-chunks-400.jsonl), [`-overlap-0`](data/knowledge-20261103-203505-overlap-0.jsonl) (400, no overlap), [`-chunks-600`](data/knowledge-20261103-203443-chunks-600.jsonl).

## What it shows

- **The snapshot answers the questions Bleep asks it, and search finds the page.** Every expected page was in the top 5 at every chunk size, and first at 200 and 300 tokens.
- **Smaller chunks rank the right page first more often.** At 400 and 600 tokens, two questions (#2, the on-failure policy's ten-second rule, and #3, the OOM killer) put a page that mentions the subject in passing (`compose-deploy`, `running-containers`) above the page about it. A shorter chunk is more about one thing.
- **Ingest is a one-time cost.** Inside the backend image (Docker Desktop on the same laptop, `--network none`, the baked-in model), the first sync of the snapshot embedded 27 pages in 31.9 s; a second sync found all 27 unchanged and took 0.02 s, and a search took 7 ms ([output](../spikes/knowledge-chunks/ingest_in_image.txt)). An earlier build of this branch took 33.5 s for the first sync, so expect a few seconds either way. Later starts skip every page whose content hash is unchanged and embed nothing.
- **Search is cheap.** 6 ms for the query embedding plus a matrix product over 444 vectors.

## Limits

- **20 questions, written by the person who chose the pages.** They name each page's subject plainly. Questions from a real incident are vaguer, and some have no answer in the snapshot; this check does not measure either. It is a check that retrieval works on this corpus, not a benchmark.
- **One expected page per question.** Where two pages both answer (health checks are in the Dockerfile reference, the `docker run` reference and the Compose reference), only the one named counts.
- **Docker docs only.** Repo docs and past incidents are searched the same way but are not in this check.
- **One machine.** The retrieval runs were outside Docker. The ingest in the backend image was timed on two builds, once each.

## Reproduce

```bash
cd backend
uv run python -m evals.knowledge                                   # Bleep's settings
uv run python -m evals.knowledge --max-tokens 400 --label chunks-400
uv run python -m evals.knowledge --overlap 0 --label overlap-0
```

The ingest in the image:

```bash
docker build -t bleep-backend backend
docker run --rm --network none -v "$PWD/docs/spikes/knowledge-chunks:/bench:ro" \
    --entrypoint python bleep-backend /bench/ingest_in_image.py
```
