# Embeddings: a local model in-process, an endpoint as the option

> **Answer:** long-term memory embeds with `fastembed` running `BAAI/bge-small-en-v1.5` (384 dimensions, the quantised ONNX build, 64 MB) on the CPU inside the backend, with two onnxruntime threads. Measured inside the backend image on an Apple M4 Pro (Docker Desktop, 14 CPUs given to the VM): the model loads in 0.2 s, one incident text embeds in 11 ms using 22 ms of CPU, and the process grows by about 160 MB once the model is loaded. The model is fetched at build time, so recall works offline. The backend image grows from 605 MB to 873 MB (+268 MB); the dependencies alone are +198 MB and the model +71 MB, and dropping the uv download cache from the image in the same change won back 131 MB. Any OpenAI-compatible `/embeddings` endpoint (OpenAI, LM Studio, Ollama) is the option, set with `EMBEDDINGS_PROVIDER=openai`; it was not measured here.

## Question

Memory needs a vector for every incident report and for every recall query Bleep must offer a local option and run with no paid service. Which model, run where, and what does it cost the laptop Bleep runs on: CPU per embed, memory, image size?

## What we tried

- **`fastembed` with `BAAI/bge-small-en-v1.5`** (the default candidate). fastembed runs ONNX models through onnxruntime with no PyTorch; the model it ships for this name is Qdrant's quantised export (`Qdrant/bge-small-en-v1.5-onnx-Q`). It installed under Python 3.14 on macOS and in the `python:3.14-slim` arm64 image with no build step (fastembed 0.9.1, onnxruntime 1.31.1, numpy 2.5.4).
- **Where the model file lives.** Fetched at build time into `/app/models` (`FASTEMBED_CACHE_PATH`) by a `RUN` that embeds one word, versus downloaded on first use into the data volume. Build time won: see "Decided along the way".
- **Thread counts.** onnxruntime's default (one thread per core) against 1, 2 and 4 threads, inside the image.
- **Scores.** Four Gather-style queries (`"<service or host>: <signal kinds>"`) against five stored incident texts (`"<title>. Cause: <cause> Action: <action>."`), to see how far apart similar and unrelated incidents land.
- **Image size.** The backend image built from `main` before this change, and with this change, each with and without the uv cache in the image.

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

## What it shows

- **The local model is cheap enough to never think about.** An incident embeds once when its report is written and once per recall, so a whole incident costs well under a tenth of a second of CPU. Re-embedding 1,000 past incidents after a model change takes about 9 s at 8.6 ms each in batches.
- **onnxruntime's default spends five times the CPU for half the wall time.** With every core it spins its threads: 6 ms of wall time cost about 100 ms of CPU. Two threads cost 22 ms of CPU for 11 ms. Bleep runs on a laptop next to the services it watches and is never in a hurry for a vector, so it uses two (`LOCAL_THREADS`).
- **The model is loaded on first use, not at boot.** The backend's memory grows by about 160 MB at the first recall (Gather of the first incident), not at startup; a Bleep that has seen no incident since boot does not carry it.
- **The right incident is always the closest, but unrelated ones still score 0.49 to 0.80.** `min_score=0.5` (the value used) therefore removes almost nothing with this model; what keeps Gather's three short is `k=3` and the same-service-first order. A died container on another service (#5 at 0.80) is the closest outsider, which is the useful kind of neighbour.
- **Most of the image growth is onnxruntime and numpy, not the model.** The model is 71 MB of the 268 MB.

## Decided along the way

- **The model is baked into the image.** The first recall then needs neither the network nor a wait, and Hugging Face being slow or down cannot fail an incident. The `RUN` that fetches it also makes the files readable by the non-root user; without that, the cached file tree was unreadable and fastembed logged a warning on every load.
- **Two threads** (above).
- **The uv cache leaves the image** (`UV_NO_CACHE=1`). It held a second copy of every wheel; dropping it pays for half of what the embeddings add.
- **The vectors are float32 blobs in SQLite, scored by brute force with numpy.** One 384-dimension vector is 1.5 KB, and a dot product over a few thousand of them is far below a millisecond, so no vector index is needed for a few hosts' incidents.
- **The embedder's name is stored with every vector** (`fastembed:BAAI/bge-small-en-v1.5`, `openai:<model>`). A record from another model is left out of recall, and `MemoryStore.reembed` embeds it again on the next boot, because two models' vectors cannot be compared.
- **The endpoint option takes any OpenAI-compatible server**: `EMBEDDINGS_PROVIDER=openai`, `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL` and, when the server wants one, `EMBEDDINGS_API_KEY`. Bleep refuses to start when the URL or the model is missing. The key stays in the environment, never in the database.

## Limits

- **One laptop.** The CPU numbers are from an M4 Pro through Docker Desktop's Linux VM. A small x86 server will be slower per text; the incident path still embeds only a few texts.
- **The score sample is small** (four queries, five records). It shows the order is right and the threshold is loose; it is not a retrieval benchmark. Host incidents (`a: disk`) separate less than service incidents, because the query has only the host id and a signal kind to go on.
- **The endpoint option is not measured here.** Its cost and latency are the provider's; with it, nothing local is loaded, but the model files stay in the image.
- **English model.** Log text and causes in other languages embed less well.

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
# the one test that runs the real model (it downloads it on a first run outside the image):
cd backend && BLEEP_REAL_EMBEDDINGS=1 uv run pytest tests/memory -k real_local_model
```
