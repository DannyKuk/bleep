# Embeddings: a local model in-process, an endpoint as the option

> **Answer:** long-term memory embeds with `fastembed` running `BAAI/bge-small-en-v1.5` (384 dimensions, the quantised ONNX build, 64 MB) on the CPU inside the backend, with two onnxruntime threads. Measured inside the backend image on an Apple M4 Pro (Docker Desktop, 14 CPUs given to the VM): the model loads in 0.2 s, one incident text embeds in 11 ms using 22 ms of CPU, and the process grows by about 160 MB once the model is loaded. The model is fetched at build time, so recall works offline. The backend image grows from 605 MB to 873 MB (+268 MB); the dependencies alone are +198 MB and the model +71 MB, and dropping the uv download cache from the image in the same change won back 131 MB. Any OpenAI-compatible `/embeddings` endpoint (OpenAI, LM Studio, Ollama) is the option, set with `EMBEDDINGS_PROVIDER=openai`; it was not measured here. **Multilingual (#146): bge-small stays the default, and `BAAI/bge-m3` is the option for setups whose logs and runbooks are mostly in another language, behind that endpoint.** Against bge-small, bge-m3 put the right Docker docs page first for 8 of 11 German and mixed-language questions (bge-small 4) but for 16 of 20 English ones (bge-small 20), and ranked the right past incident first in 15 of 18 English and German recall queries (bge-small 13). Inside the image it costs 320 ms of CPU per text (14 times), 566 s for the first ingest of the snapshot (33 s), 1.56 GB loaded (0.21 GB) and 4.44 GB of image (925 MB). It runs through fastembed only as a custom model; the endpoint route (Ollama serves `bge-m3`) was not measured. **Recall's threshold is the model's (#306):** bge-m3's right past incidents scored as low as 0.39, so recall cuts off at 0.35 for it (18 of 18 returned, 17 at 0.5) and stays at 0.5 for bge-small; `EMBEDDINGS_MIN_SCORE` sets it for any model. **German or mixed-language setups (#377): `google/gemini-embedding-2` through OpenRouter is the recommended option, bge-small stays the default for installs without a key.** On 22 queries over sloppy German service docs it put the right chunk first for 22 (bge-small 18), on #146's 11 German questions first for 9 (bge-small 4, bge-m3 8), and ranked the right past incident in the top 5 for 18 of 18 recall queries (17). **It loses one English first hit: 19 of 20 against bge-small's 20 of 20 (top 5 equal at 20).** Recall cuts off at 0.65 for it (18 of 18 right returned, 50 of 162 unrelated pass; at 0.5, 161 pass). It costs about 420 ms per embed over the network (15 ms), 3,072-float vectors (384) and a key; a full first ingest is about 85,000 tokens, about $0.017 at $0.20 per million (OpenRouter's models API, `pricing.prompt`, read 2026-11-10). OpenRouter being unreachable fails as a gap or a tool error and the incident run goes on, read from the code and not run live ([embeddings, German docs, 2026-11-10](../evals/embeddings-german-docs-2026-11-10.md)).

## Question

Memory needs a vector for every incident report and for every recall query Bleep must offer a local option and run with no paid service. Which model, run where, and what does it cost the laptop Bleep runs on: CPU per embed, memory, image size?

Later (#146): logs, commit messages and runbooks are often in another language than English, or mixed. Is a multilingual model, bge-m3 in particular, worth its cost over bge-small for memory and knowledge search?

## What we tried

- **`fastembed` with `BAAI/bge-small-en-v1.5`** (the default candidate). fastembed runs ONNX models through onnxruntime with no PyTorch; the model it ships for this name is Qdrant's quantised export (`Qdrant/bge-small-en-v1.5-onnx-Q`). It installed under Python 3.14 on macOS and in the `python:3.14-slim` arm64 image with no build step (fastembed 0.9.1, onnxruntime 1.31.1, numpy 2.5.4).
- **Where the model file lives.** Fetched at build time into `/app/models` (`FASTEMBED_CACHE_PATH`) by a `RUN` that embeds one word, versus downloaded on first use into the data volume. Build time won: see "Decided along the way".
- **Thread counts.** onnxruntime's default (one thread per core) against 1, 2 and 4 threads, inside the image.
- **Scores.** Four Gather-style queries (`"<service or host>: <signal kinds>"`) against five stored incident texts (`"<title>. Cause: <cause> Action: <action>."`), to see how far apart similar and unrelated incidents land.
- **Image size.** The backend image built from `main` before this change, and with this change, each with and without the uv cache in the image.

- **bge-m3 against bge-small (#146).** bge-m3 (570M parameters, 1,024 dimensions, BAAI's own fp32 ONNX export) is not in fastembed 0.9.1's list, so it was added with `TextEmbedding.add_custom_model` and CLS pooling ([`models.py`](../spikes/embeddings/models.py)); its vectors matched the export's own pooled output. Both models ran the 20 English knowledge questions and 11 new German and mixed-language ones over the English snapshot, 18 memory recall queries over 10 past incidents (5 written in German), and the timing scripts in the image, with bge-m3 baked in where bge-small was. Full numbers: [embeddings multilingual, 2026-11-06](../evals/embeddings-multilingual-2026-11-06.md).

The scripts are in [`docs/spikes/embeddings/`](../spikes/embeddings/); every number below is from the image built from this branch, run with `--network none` as the image's own non-root user.

## Result

Time and memory, three runs each with Bleep's setting (2 threads) and one with onnxruntime's default:

| Threads | Model load | One text (median, p95) | CPU per text | Batch of 32 | Peak memory: before, loaded, after a batch |
|---|---|---|---|---|---|
| 2 (Bleep's) | 0.19–0.21 s | 10.9 ms, 11.3–12.0 ms | 21.9–22.1 ms | 272–274 ms | 47, 208, 298 MB |
| 1 | | 17.9–18.9 ms | 17.9–18.9 ms | 527–533 ms | |
| 4 | | 6.6 ms | 26.7–26.8 ms | 148 ms | |
| 14 (default) | 0.19 s | 5.8–7.5 ms | 95–117 ms | 103–134 ms | 47, 206, 298 MB |

The same script outside Docker on the same laptop (macOS, all cores): 5.0 ms per text, 35 ms of CPU. Its first model load there, fastembed's import included, took 3.5 s.

Image size (`docker image ls`, arm64):

| Image | Size |
|---|---|
| `main` before this change | 605 MB |
| `main` without the uv cache | 474 MB |
| this change (uv cache dropped, model baked in) | 873 MB |
| this change with the uv cache left in | 1.23 GB |

Layers of the shipped image: the dependency layer grows from 163 MB to 361 MB (onnxruntime 60 MB, numpy and its libraries 68 MB, Pillow's libraries 14 MB and tokenizers 12 MB are the largest), and the model layer is 71 MB (the ONNX file is 64 MB). The uv cache was 264 MB of the 1.23 GB image.

Cosine scores of the real model (`scores.py`), queries in rows, stored incidents in columns:

| Query | #1 container died on shop | #2 url failed on shop | #3 disk on a | #4 oom on blog | #5 container died on mail |
|---|---|---|---|---|---|
| `shop: container_died` | **0.87** | 0.72 | 0.65 | 0.61 | 0.80 |
| `shop: url_failed` | 0.70 | **0.82** | 0.56 | 0.54 | 0.61 |
| `a: disk` | 0.56 | 0.51 | **0.66** | 0.58 | 0.57 |
| `blog: oom` | 0.56 | 0.52 | 0.49 | **0.74** | 0.58 |

bge-m3 against bge-small (#146), inside the images on two threads unless noted; retrieval scores from macOS:

| | bge-small | bge-m3 |
|---|---|---|
| Knowledge, 20 English questions: first, top 5 | 20, 20 | 16, 20 |
| Knowledge, 11 German and mixed questions: first, top 5 | 4, 11 | 8, 11 |
| Memory, 18 English and German queries: first, returned by Bleep's recall | 13, 17 | 15, 17 |
| Unrelated records scoring over `min_score` 0.5 | 158 of 162 | 36 of 162 |
| One text, CPU per text | 11 ms, 22 ms | 158 ms, 320 ms |
| First ingest of the snapshot (444 chunks) | 33.4 s | 565.8 s |
| Peak memory: loaded, at the ingest's peak | 0.21 GB, 0.84 GB | 1.56 GB, 3.02 GB |
| Image (`docker image ls`), compressed content | 925 MB, 239 MB | 4.44 GB, 1.53 GB |

## What it shows

- **The local model is cheap enough to never think about.** An incident embeds once when its report is written and once per recall, so a whole incident costs well under a tenth of a second of CPU. Re-embedding 1,000 past incidents after a model change takes about 9 s at 8.6 ms each in batches.
- **onnxruntime's default spends five times the CPU for half the wall time.** With every core it spins its threads: 6 ms of wall time cost about 100 ms of CPU. Two threads cost 22 ms of CPU for 11 ms. Bleep runs on a laptop next to the services it watches and is never in a hurry for a vector, so it uses two (`LOCAL_THREADS`).
- **The model is loaded on first use, not at boot.** The backend's memory grows by about 160 MB at the first recall (Gather of the first incident), not at startup; a Bleep that has seen no incident since boot does not carry it.
- **The right incident is always the closest, but unrelated ones still score 0.49 to 0.80.** `min_score=0.5` (the value used) therefore removes almost nothing with this model; what keeps Gather's three short is `k=3` and the same-service-first order. A died container on another service (#5 at 0.80) is the closest outsider, which is the useful kind of neighbour.
- **Most of the image growth is onnxruntime and numpy, not the model.** The model is 71 MB of the 268 MB.
- **bge-m3 trades English first places for German ones, at a cost Bleep would carry on every install.** Over all 31 knowledge questions both models put the right page first for 24 and in the top 5 for 31, and Investigate reads the top 5. In memory it ranks the right incident first a little more often and separates it better (+0.10 against +0.04), but it also dropped one under the 0.5 threshold, which was set for bge-small. Most installs gain nothing from it and would pay 14 times the CPU per text, a 9-minute first start and 3.5 GB more image (#146).

## Decided along the way

- **The model is baked into the image.** The first recall then needs neither the network nor a wait, and Hugging Face being slow or down cannot fail an incident. The `RUN` that fetches it also makes the files readable by the non-root user; without that, the cached file tree was unreadable and fastembed logged a warning on every load.
- **Two threads** (above).
- **The uv cache leaves the image** (`UV_NO_CACHE=1`). It held a second copy of every wheel; dropping it pays for half of what the embeddings add.
- **The vectors are float32 blobs in SQLite, scored by brute force with numpy.** One 384-dimension vector is 1.5 KB, and a dot product over a few thousand of them is far below a millisecond, so no vector index is needed for a few hosts' incidents.
- **The embedder's name is stored with every vector** (`fastembed:BAAI/bge-small-en-v1.5`, `openai:<model>`). A record from another model is left out of recall, and `MemoryStore.reembed` embeds it again on the next boot, because two models' vectors cannot be compared.
- **bge-small stays the default; bge-m3 is the multilingual option behind the endpoint, not baked in** (#146). A second baked-in model would grow every image for the few setups that need it; an endpoint (Ollama lists `bge-m3`) costs Bleep's image nothing. Switching the default is a code change for its own issue; `min_score` became the model's in #306 (below).
- **`min_score` belongs to the embedder** (#306). bge-small keeps 0.5, bge-m3 gets 0.35 (one 0.025 step below 0.375, the highest at which all 18 right records came back), matched by name so Ollama's `bge-m3:latest` counts too; another model keeps 0.5, and `EMBEDDINGS_MIN_SCORE` sets it for any ([min score](../evals/embeddings-min-score-2026-11-06.md)).
- **gemini-embedding-2 through OpenRouter is the recommended option for German or mixed-language docs; bge-small stays the keyless default** (#377). The four settings are in [setup](../setup.md#memory-embeddings-optional). Reason: first for 22 of 22 on the German set and 9 of 11 on #146's German questions, against 18 and 4 for bge-small, and no top-5 loss on English, without bge-m3's 3.5 GB of image or its CPU. Its `min_score` is 0.65, one 0.025 step below 0.675, the highest at which all 18 right past incidents came back (`MIN_SCORES`, matched by name, so `google/gemini-embedding-2` counts); at 0.5 it filters nothing (161 of 162 unrelated records pass).
- **The endpoint option takes any OpenAI-compatible server**: `EMBEDDINGS_PROVIDER=openai`, `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL` and, when the server wants one, `EMBEDDINGS_API_KEY`. Bleep refuses to start when the URL or the model is missing. The key stays in the environment, never in the database.

## Limits

- **One laptop.** The CPU numbers are from an M4 Pro through Docker Desktop's Linux VM. A small x86 server will be slower per text; the incident path still embeds only a few texts.
- **The score sample is small** (four queries, five records). It shows the order is right and the threshold is loose; it is not a retrieval benchmark. Host incidents (`a: disk`) separate less than service incidents, because the query has only the host id and a signal kind to go on.
- **The endpoint option is not measured here.** Its cost and latency are the provider's; with it, nothing local is loaded, but the model files stay in the image.
- **English model.** Log text and causes in other languages embed less well: on 11 German and mixed-language questions bge-small put the right page first for 4 and in the top 5 for all 11, and it scores every incident 0.5 or more against every other, so `min_score` filters nothing (#146).
- **bge-m3 was measured in-process only, in fp32, in German only** (#146). Its cost and scores through Ollama, a quantised build, other multilingual models fastembed lists, and chunk sizes chosen for it were not measured. Its threshold of 0.35 rests on 18 queries, and on one score at its edge: Gather's short query scored a right German record 0.39 (#306).
- **gemini-embedding-2 was measured once, from one laptop, on small sets** (#377). The German set is 22 queries over 31 chunks written by one agent, the recall threshold rests on 18 queries over 10 records, and token counts are estimates (the run's real spend was about $0.02; the price is OpenRouter's models API, `pricing.prompt` 0.0000002 per token, read 2026-11-10).
- **It loses one English first hit** (19 of 20 against 20 of 20), though its top 5 is equal.
- **Every embed is a network call** of about 420 ms median (bge-small 15 ms in-process), a first ingest of the snapshot is 444 texts in batches of 32, and the vectors are 8 times larger in SQLite (12 KB against 1.5 KB). It needs a key (`EMBEDDINGS_API_KEY`) and a service Bleep does not control; the embedding calls are not in the usage ledger.
- **OpenRouter being unreachable was read from the code, not run live.** `OpenAICompatEmbedder.embed` turns an HTTP error or a timeout into `EmbedderError`, after up to 30 s (`HTTP_TIMEOUT_S`) per failed call. (a) Gather's recall becomes a gap in the incident's context (`graph/nodes/gather.py`, `_recall`). (b) `knowledge_search` and `recall_incidents` answer the model "the tool failed (EmbedderError)" (`_invoke` in `graph/nodes/investigate.py`) and the run goes on. (c) Saving the finished incident to memory is logged and lost (`_remember` in `graph/nodes/report.py`; the embed happens before the database transaction, so nothing is half written), and the report is still sent. (d) Knowledge ingest runs in the background and tries again (`KnowledgeRefresher._safely` and `run` in `knowledge/ingest.py`), and a failed re-embed at boot is logged (`_reembed` in `main.py`), leaving old records out of recall until the next boot.

## Reproduce

```bash
docker build -t bleep-backend backend
docker image ls bleep-backend
docker run --rm --network none -v "$PWD/docs/spikes/embeddings:/bench:ro" \
    --entrypoint python bleep-backend /bench/embed_bench.py
docker run --rm --network none -e THREADS=14 -v "$PWD/docs/spikes/embeddings:/bench:ro" \
    --entrypoint python bleep-backend /bench/embed_bench.py
docker run --rm --network none -v "$PWD/docs/spikes/embeddings:/bench:ro" \
    --entrypoint python bleep-backend /bench/scores.py
# bge-m3 against bge-small (#146): see docs/evals/embeddings-multilingual-2026-11-06.md
# recall's threshold per model (#306): see docs/evals/embeddings-min-score-2026-11-06.md
# German docs, gemini-embedding-2 against bge-small (#377): see docs/evals/embeddings-german-docs-2026-11-10.md
docker build -f docs/spikes/embeddings/m3.Dockerfile --build-context spike=docs/spikes/embeddings \
    -t bleep-spike-146:m3 backend
docker run --rm --network none -e MODEL=m3 -v "$PWD/docs/spikes/embeddings:/bench:ro" \
    --entrypoint python bleep-spike-146:m3 /bench/embed_bench.py
# the one test that runs the real model (it downloads it on a first run outside the image):
cd backend && BLEEP_REAL_EMBEDDINGS=1 uv run pytest tests/memory -k real_local_model
```
