# bge-m3 against bge-small for multilingual search, 2026-11-06

> **Answer:** bge-m3 finds German text better and English text worse, at about 14 times the CPU and 3.5 GB more image. On 11 new German and mixed-language questions over the English Docker docs it put the right page first for **8 of 11** where bge-small did for **4 of 11**, but on the 20 English questions for **16 of 20** against **20 of 20**; over all 31 both put it first for 24 and in the top 5 for 31. In memory recall over 10 past incidents, 5 in German, it ranked the right one first in **15 of 18** queries against 13 of 18, and Bleep's recall returned it in 17 of 18 with both. Inside the backend image, on two threads, one text costs **158 ms and 320 ms of CPU** against 11 ms and 22 ms, the snapshot's first ingest takes **566 s** against 33 s, the loaded model holds **1.56 GB** against 0.21 GB, and the image grows from 925 MB to **4.44 GB**. bge-m3 runs through fastembed's `TextEmbedding` as a custom model from BAAI's own ONNX export; fastembed does not list it. Bleep keeps bge-small; bge-m3 is the option for multilingual setups, behind the endpoint ([embeddings](../decisions/embeddings.md)).

## Setup

- **Models**: `BAAI/bge-small-en-v1.5` as Bleep ships it (fastembed's quantised ONNX, 384 dimensions), and `BAAI/bge-m3` (570M parameters, 1,024 dimensions, fp32) from the `onnx/` folder of BAAI's repo at commit `5617a9f`. fastembed 0.9.1 does not list bge-m3 in `TextEmbedding.list_supported_models()` (nor as a sparse or late-interaction model), so [`models.py`](../spikes/embeddings/models.py) adds it with `TextEmbedding.add_custom_model` and CLS pooling, as the repo's `1_Pooling/config.json` says. Its vectors matched the export's own `sentence_embedding` output at cosine 1.0 on two texts. Both run through Bleep's `FastEmbedEmbedder` on two threads, onnxruntime 1.31.1.
- **Knowledge questions**: the 20 English questions of [knowledge, 2026-11-03](knowledge-2026-11-03.md), and 11 added for this check (M1 to M11 below) in [`questions_multilingual.yaml`](../../backend/evals/knowledge/questions_multilingual.yaml): M1 to M5 in German, the same subjects as English #3, #8, #13, #18 and #14 in other words; M6 to M10 German questions around English output as Docker prints it (`"OOMKilled": true`, `dependency failed to start`, `Restarting (1)`, `"FailingStreak": 3`, `no space left on device`); M11 an English question around a German application log line. The snapshot stays English, with Bleep's chunk settings (300 tokens, overlap 50, 444 chunks). Runner: [`retrieval.py`](../spikes/embeddings/retrieval.py), which calls `evals.knowledge.run`.
- **Memory recall**: [`recall.py`](../spikes/embeddings/recall.py). Ten stored records in `MemoryStore`'s form (`"<title>. Cause: <cause> Action: <action>."`), E1 to E5 written in English, D1 to D5 in German (title, cause and action, as a model asked to report in German would write them), and 18 queries in three groups: Gather's own query (`"<service>: <signal kinds>"`, the same in any language), a `recall_incidents` query in the record's language, and one in the other language. "In Bleep" applies recall as `MemoryStore.similar` does: scores under 0.5 dropped, then for Gather the same service's first and the top 3, for the tool the top 5.
- **Cost**: [`embed_bench.py`](../spikes/embeddings/embed_bench.py) (one text 100 times, a batch of 32 ten times, peak memory) and [`ingest.py`](../spikes/embeddings/ingest.py) (the snapshot's first sync, one search, peak memory).
- **Images**: `bleep-spike-146:small`, built from `backend/Dockerfile` at `31195c4`, and `bleep-spike-146:m3`, the same with bge-m3 baked in where bge-small was ([`m3.Dockerfile`](../spikes/embeddings/m3.Dockerfile), the one change is the model `RUN`). Both removed after the runs.
- **Machine**: the Apple M4 Pro of the 2026-11-03 numbers. Retrieval and recall ran outside Docker (macOS, as the 2026-11-03 retrieval did); the scores do not depend on where they run. Time, memory and image size were measured inside the images under Docker Desktop 29.8.2 (14 CPUs and 7.7 GB given to the VM), `--network none`, as the image's non-root user, with Bleep's stack running beside them, unless a row says macOS.
- No chat model and no hosted API was called. The `EMBEDDINGS_PROVIDER=openai` route was not measured: Ollama is not installed on this machine.

## Result

### Knowledge search

| Model | Questions | hit@1 | hit@3 | hit@5 | MRR@5 | Ingest (macOS) | Search (median, macOS) |
|---|---|---|---|---|---|---|---|
| bge-small | 20 English | **100%** (20) | 100% | 100% | **1.00** | 19.5 s | 6 ms |
| bge-m3 | 20 English | 80% (16) | 100% | 100% | 0.88 | 79.7 s | 22 ms |
| bge-small | 11 German and mixed | 36% (4) | 91% | 100% | 0.63 | | 9 ms |
| bge-m3 | 11 German and mixed | **73%** (8) | 91% | 100% | **0.84** | | 30 ms |
| bge-small | all 31 | 77% (24) | 97% | 100% | 0.87 | | |
| bge-m3 | all 31 | 77% (24) | 97% | 100% | 0.87 | | |

bge-small's 20 English questions gave the same ranks as on 2026-11-03 (ingest 19.5 s then 19.8 s).

Rank of the expected page:

| # | Question (abridged) | Expected page | bge-small | bge-m3 |
|---|---|---|---|---|
| #2 | on-failure policy, crash right after start | `restart-policies` | 1 | 2 (`compose-deploy` first) |
| #3 | OOM killer and memory limits | `resource-constraints` | 1 | 3 |
| #11 | events when a container dies or is OOM killed | `cli-events` | 1 | 2 (`resource-constraints` first) |
| #15 | temporary in-memory filesystem | `tmpfs` | 1 | 3 (`volumes`, `bind-mounts` first) |
| 16 other English | | | 1 | 1 |
| M1 | Wie begrenze ich den Arbeitsspeicher eines Containers ... | `resource-constraints` | 2 | 2 |
| M2 | Die Logdateien der Container füllen die Festplatte ... | `logging-json-file` | 1 | 1 |
| M3 | Wie entferne ich ungenutzte Images ... | `pruning` | 1 | 1 |
| M4 | Ein veröffentlichter Port ist ... nicht erreichbar | `port-publishing` | 3 (`cli-events` first) | 1 |
| M5 | Die Datenbank hat ... alle Daten verloren | `volumes` | 3 (`cli-events` first) | 1 |
| M6 | ... `"OOMKilled": true` und `"ExitCode": 137` | `resource-constraints` | 4 | 5 |
| M7 | ... `dependency failed to start: container shop-db is unhealthy` | `compose-startup-order` | 2 | 1 |
| M8 | ... `Restarting (1) 5 seconds ago` ... | `restart-policies` | 2 (`cli-events` first) | 1 |
| M9 | ... `"Status": "unhealthy", "FailingStreak": 3` ... | `dockerfile-healthcheck` | 1 | 1 |
| M10 | ... `no space left on device` ... Größe der Container-Logs | `logging-json-file` | 1 | 2 (`live-restore` first) |
| M11 | shop-web logs `FEHLER: Verbindung zu shop-db:5432 abgelehnt` ... | `compose-startup-order` | 2 (`cli-events` first) | 1 |

### Memory recall

| Group | Queries | hit@1 small | hit@1 m3 | hit@3 small | hit@3 m3 | Margin small | Margin m3 | In Bleep small | In Bleep m3 |
|---|---|---|---|---|---|---|---|---|---|
| Gather's query | 6 | 3 | 4 | 6 | 6 | +0.02 | +0.05 | 6 | 5 |
| same language | 5 | 5 | 5 | 5 | 5 | +0.10 | +0.15 | 5 | 5 |
| other language | 7 | 5 | 6 | 6 | 7 | +0.02 | +0.10 | 6 | 7 |
| **all** | 18 | **13** | **15** | 17 | **18** | +0.04 | +0.10 | 17 | 17 |

The margin is the right record's score minus the best other record's, averaged. The two misses in Bleep's recall:

- **bge-small** cut `container killed after it went over its memory limit` (German record D2) to sixth: five records scored above it (0.64), the closest an English one about postfix (0.75), so the tool's top 5 left it out.
- **bge-m3** dropped D2 for Gather's `wiki: oom`: it scored 0.39, under recall's `min_score` of 0.5. bge-small scored it 0.64 and put it first, as the same service's record.

Scores of the records that are not the answer, against recall's `min_score` of 0.5: bge-small put **158 of 162** of them over it, bge-m3 **36 of 162**. The right records averaged 0.75 with bge-small and 0.65 with bge-m3.

### Cost

In the images, two threads (Bleep's), three runs each of `embed_bench.py`:

| Model | Model load | One text (median, p95) | CPU per text | Batch of 32 | Peak memory: before, loaded, after a batch |
|---|---|---|---|---|---|
| bge-small | 0.22–0.24 s | 11.0–11.1 ms, 11.5–11.8 ms | 22.2–22.4 ms | 279–280 ms | 51, 209–212, 295–299 MB |
| bge-m3 | 0.98–1.24 s | 157.9–160.9 ms, 161.8–186.1 ms | 317.0–327.1 ms | 4,906–4,921 ms | 51, 1,549–1,564, 1,647–1,656 MB |
| bge-m3, 14 threads (one run) | 1.21 s | 185.6 ms, 228.9 ms | 2,587.6 ms | 2,760 ms | 51, 1,565, 1,657 MB |

The first ingest of the snapshot (27 pages, 444 chunks) in the images, `ingest.py`, once each, model load included:

| Model | First sync | CPU | Search | Peak memory |
|---|---|---|---|---|
| bge-small | 33.4 s | 66.7 s | 7 ms | 841 MB |
| bge-m3 | **565.8 s** | 1,129.8 s | 35 ms | **3,016 MB** |

The same scripts on macOS outside Docker, two threads, once: bge-small 7.1 ms per text (14.2 ms of CPU), bge-m3 31.3 ms (62.7 ms of CPU) and 1.7 GB loaded; the whole bge-m3 retrieval run (two ingests, 31 questions) peaked at 4.5 GB resident. bge-m3 is five times slower in the Linux image than on macOS on the same cores and the same onnxruntime version; bge-small 1.6 times. Why was not looked into.

Image size (`docker image ls`, Docker Desktop's containerd store, arm64):

| Image | Size | Content (compressed, what a pull downloads) | Model layer |
|---|---|---|---|
| bge-small (Bleep's, `31195c4`) | 925 MB | 239 MB | 71.5 MB |
| bge-m3 in its place | **4.44 GB** | **1.53 GB** | **2.29 GB** |

The `Size` column of this store counts the unpacked layers and the compressed content together; the running `bleep-backend:latest` shows the same 925 MB.

Raw data, a totals line and one JSON line per question or query: [`small-english`](data/embeddings-multilingual-small-english.jsonl), [`small-multilingual`](data/embeddings-multilingual-small-multilingual.jsonl), [`m3-english`](data/embeddings-multilingual-m3-english.jsonl), [`m3-multilingual`](data/embeddings-multilingual-m3-multilingual.jsonl), [`small-recall`](data/embeddings-multilingual-small-recall.jsonl), [`m3-recall`](data/embeddings-multilingual-m3-recall.jsonl).

## What it shows

- **bge-m3 trades English first places for German ones.** Over the 31 questions both found 24 first and all 31 in the top 5. Investigate reads the best section of each of the 5 pages, so for knowledge search the right page reached the model with either model on every question.
- **bge-m3 gains on both kinds of new question:** first for 4 of the 5 German ones (bge-small 2) and 4 of the 6 mixed ones (bge-small 2). bge-small's misses mostly put `cli-events`, a page of raw event names, first (4 of the 11); it still had every German page in the top 3 and every page of the 11 in the top 5. One question defeats both: M6, German around `"OOMKilled": true` and exit code 137, ranks `resource-constraints` fourth and fifth.
- **Memory recall gains a little, and needs its threshold moved.** bge-m3 ranked the right incident first in 2 more of 18 queries and separated it from the others by +0.10 against +0.04, so `min_score` would finally filter something (only 36 of 162 unrelated scores pass 0.5, against 158). But it also dropped a right record at 0.39; with bge-m3, 0.5 is too high for Gather's short query. Bleep now cuts off at 0.35 for bge-m3, measured on these queries in [min score](embeddings-min-score-2026-11-06.md) (#306).
- **The cost is not small any more.** 320 ms of CPU per text is still nothing per incident (two or three texts), but the first start embeds the snapshot for 9.4 minutes on two threads, a model change re-embeds every stored incident at about 150 ms each in batches (1,000 incidents: about 2.5 minutes), the backend holds 1.35 GB more while the model is loaded (1.56 GB) and 3 GB at the ingest's peak, and every pull of the image downloads 1.3 GB more.
- **More threads do not help one text.** On 14 threads one text took longer (186 ms) for eight times the CPU; only the batch got faster.

## Limits

- **Few questions, written by one person.** 11 multilingual questions and 18 recall queries over 10 records, all written for this check; one rank more or less moves hit@1 by 9 points for the 11. It shows a direction, not a size.
- **German only.** No other language was tried. The snapshot is English; German runbooks in a linked repo were not tried, and neither were German titles from Bleep itself.
- **The chunk settings were chosen on bge-small** ([knowledge chunks](../decisions/knowledge-chunks.md)). bge-m3 reads 8,192 tokens, and another chunk size may suit it better; not tried.
- **bge-m3 in fp32 only.** BAAI's export is the only one tried; a quantised build would be smaller and faster and was not measured. Nor were the other multilingual models fastembed lists (`paraphrase-multilingual-MiniLM-L12-v2`, 0.22 GB; `intfloat/multilingual-e5-large`, 2.24 GB).
- **The endpoint route was not measured.** Ollama, which lists `bge-m3` in its library (567M parameters, 1.2 GB, [ollama.com/library/bge-m3](https://ollama.com/library/bge-m3)) and serves `/v1/embeddings` ([OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)), is not installed here, so neither its scores nor its cost were taken.
- **One machine, one run of each ingest.** The bge-m3 gap between macOS and the Linux image is measured but not explained.

## Reproduce

```bash
cd backend
MODEL=small uv run python ../docs/spikes/embeddings/retrieval.py
FASTEMBED_CACHE_PATH=/some/cache MODEL=m3 uv run python ../docs/spikes/embeddings/retrieval.py
MODEL=small uv run python ../docs/spikes/embeddings/recall.py
FASTEMBED_CACHE_PATH=/some/cache MODEL=m3 uv run python ../docs/spikes/embeddings/recall.py
uv run python -m evals.knowledge --questions evals/knowledge/questions_multilingual.yaml  # bge-small
cd ..
docker build -t bleep-spike-146:small backend
docker build -f docs/spikes/embeddings/m3.Dockerfile --build-context spike=docs/spikes/embeddings \
    -t bleep-spike-146:m3 backend
docker image ls bleep-spike-146
for m in small m3; do
  docker run --rm --network none -e MODEL=$m -v "$PWD/docs/spikes/embeddings:/bench:ro" \
      --entrypoint python bleep-spike-146:$m /bench/embed_bench.py
  docker run --rm --network none -e MODEL=$m -v "$PWD/docs/spikes/embeddings:/bench:ro" \
      --entrypoint python bleep-spike-146:$m /bench/ingest.py
done
```
