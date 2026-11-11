# bge-m3 on a local server (LM Studio), 2026-11-11

> **Answer:** `bge-m3` served by LM Studio, a local OpenAI-compatible server that needs no key, gives the multilingual results of #146 and #377's `google/gemini-embedding-2` on German docs, for free and without leaving the machine. On the 22 queries over sloppy German service docs it put the right chunk first for **22 of 22** (bge-small 18, gemini-embedding-2 22), on #146's 11 German questions first for **8 of 11** (bge-small 4), and on its 20 English questions first for **16 of 20** (bge-small 20, so it still loses four English first hits; top 5 is 20 of 20 for both). Its recall scores are the in-process model's: the lowest right past incident scores 0.386, the highest step that still returns every right record is 0.375, so the `MIN_SCORES` entry of **0.35 is confirmed** and 18 of 18 right records come back at it (17 of 18 at 0.5). The first ingest of the Docker docs snapshot (444 chunks) took **14.8 s**, where bge-m3 in-process on two CPU threads took 566 s (#146), because LM Studio runs it on the laptop's GPU. The second point, `text-embedding-nomic-embed-text-v1.5` (already installed), is clearly worse for this: 18 of 22 first on the German set, 10 of 18 first on recall, and at the default cut-off of 0.5 it loses a right record and passes 93 of 162 unrelated ones (#442).

## Setup

- **Server**: LM Studio (Bionic.app 1.1.10+8) on macOS, an Apple M4 Pro, started with `lms server start` for the run and stopped with `lms server stop` after it. It picked port 1235 here (1234 is LM Studio's usual default; the port is in its Developer settings); the harness was pointed at `http://localhost:1235/v1`.
- **Models**: `bge-m3` as `bge-m3-FP16.gguf` (1.16 GB, from the `gpustack/bge-m3-GGUF` repository on Hugging Face, `lms get`), which LM Studio serves as `text-embedding-bge-m3` (1,024 dimensions); and `text-embedding-nomic-embed-text-v1.5` (84 MB, 768 dimensions), which was already installed. No search or document prefixes were added for either: Bleep does not send any, so this is what Bleep would do. nomic-embed-text is trained with `search_query:` and `search_document:` prefixes, so its numbers are what Bleep gets from it today, not its best.
- **Harness**: [`measure.py`](../spikes/embeddings-german/measure.py) with its new `server` provider, which is Bleep's own `OpenAICompatEmbedder` with no key, so the harness measures what `EMBEDDINGS_PROVIDER=server` does. It runs #377's set (22 queries over 31 chunks, cut and indexed as a repo's docs are), #146's 20 English and 11 German and mixed-language questions over the Docker docs snapshot (27 pages, 444 chunks) and #306's 18 recall queries over 10 records. No chat model was called and nothing was paid for: the models are local.
- **Run**: 2026-11-11, one run each; the bge-m3 run was repeated once after `min_score_for` learned LM Studio's `text-embedding-` prefix (below). The repeat gave the same ranks, recall numbers, sweep and ingest time; only the latency figures differ a little between the two runs (first call, one text, batch of 31), and the figures here and in the raw data are the repeat's.
- **Raw data**: [bge-m3](data/embeddings-local-server-bge-m3.json), [nomic-embed-text-v1.5](data/embeddings-local-server-nomic-embed-text-v1.5.json) (every query's top 5 and the full sweep).
- **Check against earlier runs**: bge-m3 here is first for 16 of 20 and 8 of 11 and ranks the right incident first for 15 of 18, as the in-process fp32 model did in #146, and its sweep tops out at the same 0.375 as #306's, so the GGUF F16 build through LM Studio behaves as the ONNX one.

## Result

Right chunk (or page, or record) first, and in the top 5. The bge-small and gemini-embedding-2 columns are from [#377's report](embeddings-german-docs-2026-11-10.md).

| Set | Queries | bge-small | gemini-embedding-2 | bge-m3 (LM Studio) | nomic-embed-text-v1.5 (LM Studio) |
|---|---|---|---|---|---|
| New German set, all | 22 | 18, 19 | 22, 22 | **22, 22** | 18, 18 |
| New set, English queries | 15 | 14, 15 | 15, 15 | 15, 15 | 14, 14 |
| New set, German queries | 7 | 4, 4 | 7, 7 | **7, 7** | 4, 4 |
| #146 English knowledge questions | 20 | 20, 20 | 19, 20 | 16, 20 | 19, 20 |
| #146 German and mixed knowledge questions | 11 | 4, 11 | 9, 11 | 8, 11 | 7, 11 |
| #306 recall queries, record ranked | 18 | 13, 17 | 14, 18 | 15, 18 | 10, 15 |

### Recall's cut-off

| | bge-small | gemini-embedding-2 | bge-m3 (LM Studio) | nomic-embed-text-v1.5 (LM Studio) |
|---|---|---|---|---|
| Lowest score of a right record | 0.636 | 0.692 | 0.386 | 0.497 |
| Highest score of an unrelated record | 0.816 | 0.768 | 0.637 | 0.776 |
| Highest threshold (0.025 steps) returning every right record the top 5 allows | 0.625 | 0.675 | **0.375** | 0.475 |
| Right returned at 0.5 | 17 of 18 | 18 of 18 | 17 of 18 | 14 of 18 (15 allowed) |
| Unrelated records over 0.5 (of 162) | 158 | 161 | 35 | 93 |
| `MIN_SCORES` entry | 0.5 | 0.65 | **0.35** (confirmed) | none: 0.5 |
| Right returned, unrelated passing at the entry | 17, 158 | 18, 50 | 18, 146 | 14, 93 |

One step below the highest threshold is #306's rule, and 0.375 is the highest for bge-m3 here as in #306, so 0.35 stands: at it all 18 right records come back and 146 of 162 unrelated ones pass, which is how it was in-process (147 of 162). Sweep for bge-m3, right returned and unrelated passing:

| min_score | 0.300 | 0.350 | 0.375 | 0.400 | 0.450 | 0.500 | 0.550 | 0.600 |
|---|---|---|---|---|---|---|---|---|
| Right returned (of 18) | 18 | 18 | 18 | 17 | 17 | 17 | 16 | 13 |
| Unrelated passing (of 162) | 161 | 146 | 132 | 118 | 74 | 35 | 15 | 7 |

For nomic-embed-text, 0.5 loses a right record that 0.475 keeps, and no threshold separates the records well (right ones from 0.497, unrelated ones up to 0.776), so it has no entry in `MIN_SCORES`; `EMBEDDINGS_MIN_SCORE` sets one.

### Cost and speed

| | bge-small (in-process) | gemini-embedding-2 (OpenRouter) | bge-m3 (LM Studio) | nomic-embed-text-v1.5 (LM Studio) |
|---|---|---|---|---|
| Vector size | 384 | 3,072 | 1,024 (4 KB as float32) | 768 |
| First call | 168 ms | 664 ms | 411 ms | 330 ms |
| One text, median (max) | 15 ms (20 ms) | 420 ms (483 ms) | 23 ms (29 ms) | 15 ms (34 ms) |
| Batch of 31 | 648 ms | 986 ms | 626 ms | 392 ms |
| First ingest of the Docker docs snapshot (444 chunks) | 33 s (in the image) | about 85,000 tokens | **14.8 s** (566 s in-process, #146) | 6.4 s |
| Money | none | $0.20 per million tokens | none | none |

The bge-m3 run embedded 598 texts in 103 calls.

## What it shows

- **A local server is the multilingual option that costs nothing and needs no key.** bge-m3 through LM Studio matches gemini-embedding-2 on the German set (22 of 22), is a little behind it on #146's German questions (8 of 11 against 9), and loses the same kind of English first hits as in-process (16 of 20), while nothing leaves the machine and no key or service is involved. gemini-embedding-2 loses only one.
- **The cost that ruled bge-m3 out as a default is the in-process one, not the model's.** In #146 it took 566 s for the first ingest, 320 ms of CPU per text and 4.4 GB of image inside the backend; on a server that uses the GPU the same ingest took 15 s, one text 23 ms, and the backend's image is unchanged. The server's memory is the server's (the F16 file is 1.16 GB).
- **bge-small stays the default.** It is the only option that works with no setup, no server running and no network, and it keeps the best English first-hit rate (20 of 20). A server has to be running whenever Bleep embeds (see Limits), so it is an opt-in.
- **LM Studio names models `text-embedding-<name>`**, which `min_score_for` did not match to bge-m3's 0.35: Bleep would have used 0.5 and dropped a right past incident. It now removes that prefix and an `@` variant suffix (`text-embedding-bge-m3@fp16`) before the lookup; a test covers it.
- **nomic-embed-text through Bleep as it is today is the worse choice for German or recall**, and no better on English (19 of 20 first). It is faster and smaller, so it can stand in where English is all that is written, but bge-small, which ships in the image, does as well or better there.

## Limits

- **One laptop, one run each, on the same small sets as before.** 22 queries over 31 chunks written by one agent, 18 recall queries over 10 records. The cut-off rests on 18 queries and on one right record at 0.386.
- **A GGUF F16 build and LM Studio's runtime only.** Another quantisation (the Q8_0 file is 0.63 GB), Ollama's `bge-m3` and a server on a small x86 machine without a GPU were not measured; the speed figures belong to an M4 Pro's GPU and will be slower elsewhere. The Ollama route is the same API and was not run here.
- **The server being off is a failure, as an unreachable endpoint is.** Bleep embeds through it for every recall, every knowledge search and every saved incident, so a stopped LM Studio makes `OpenAICompatEmbedder.embed` raise `EmbedderError` and each of those fail as the endpoint's outage does ([embeddings, limits](../decisions/embeddings.md#limits)); an unreachable local address fails at once instead of after a timeout. This was read from the code and not run here. LM Studio also loads a model on its first request and can unload it when idle, so the first embed after a pause is slower.
- **No prefixes.** Models trained with them (nomic-embed-text, e5) score below what they can.
- **Port**: LM Studio chose 1235 here; the default 1234 in the docs is where it usually listens, and the real port is in LM Studio's Developer settings.

## Reproduce

```bash
lms get https://huggingface.co/gpustack/bge-m3-GGUF@FP16 --gguf -y
lms server start        # prints the port; LM Studio's usual one is 1234
cd backend && uv run python ../docs/spikes/embeddings-german/measure.py server \
    --base-url http://localhost:1234/v1 --model text-embedding-bge-m3 \
    --out ../docs/evals/data/embeddings-local-server-bge-m3.json
cd backend && uv run python ../docs/spikes/embeddings-german/measure.py server \
    --base-url http://localhost:1234/v1 --model text-embedding-nomic-embed-text-v1.5 \
    --out ../docs/evals/data/embeddings-local-server-nomic-embed-text-v1.5.json
lms server stop
```
