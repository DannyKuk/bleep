# Recall's score threshold for bge-m3, 2026-11-06

> **Answer:** with bge-m3, memory recall's `min_score` is **0.35**; bge-small keeps 0.5. On the 18 recall queries of [bge-m3 against bge-small](embeddings-multilingual-2026-11-06.md), the right past incident's lowest bge-m3 score was **0.387** (Gather's `wiki: oom` on a German record), so 0.5 dropped it and Bleep's recall returned **17 of 18**. The highest threshold in steps of 0.025 that returns all **18 of 18** is 0.375; Bleep takes one step below it, 0.35, because 0.375 sits only 0.012 under that one score. At 0.35 the 17 other right records keep their places, and 147 of the 162 records that are not the answer still pass (36 at 0.5), which is how bge-small already works: it passes 158 of 162 at 0.5, and `k` and the same-service-first order keep the answer short. bge-small's lowest right score was 0.636, so no threshold from 0.30 to 0.60 changes what it returns. Bleep sets 0.35 for `bge-m3` by name, and `EMBEDDINGS_MIN_SCORE` sets the threshold for any other model ([memory](../design/memory.md), #306).

## Setup

- **Script**: [`recall.py`](../spikes/embeddings/recall.py) as in #146 (ten stored records in `MemoryStore`'s form, five in English and five in German, 18 queries in three groups: Gather's own query, a `recall_incidents` query in the record's language, one in the other language), plus a sweep added for this check: for each threshold from 0.30 to 0.60 in steps of 0.025, how many right records Bleep's recall returns (Gather's top 3 with the same service's first, the tool's top 5) and how many records that are not the answer pass the threshold. `MIN_SCORE` sets the threshold for the per-query table.
- **Models**: `BAAI/bge-small-en-v1.5` as Bleep ships it, and `BAAI/bge-m3` from BAAI's own fp32 ONNX export as a fastembed custom model ([`models.py`](../spikes/embeddings/models.py)); fastembed 0.9.1, onnxruntime 1.31.1, two threads. Every score matched #146's raw data to three decimals, for both models.
- **Where**: macOS on the Apple M4 Pro of #146, in the backend's venv, with bge-m3 downloaded to a temporary cache; and again inside `bleep-spike-306:m3`, built from [`m3.Dockerfile`](../spikes/embeddings/m3.Dockerfile) on `f9b2596` (Docker Desktop, `--network none`), at `MIN_SCORE` 0.5 and 0.35. The image printed the same tables as macOS. The image, its build cache and the model cache were removed after the runs.
- No chat model and no hosted API was called. The endpoint route (Ollama's `bge-m3`) was not measured.

## Result

Right records returned by Bleep's recall (of 18) and other records passing the threshold (of 162):

| `min_score` | bge-m3: returned | bge-m3: others passing | bge-small: returned | bge-small: others passing |
|---|---|---|---|---|
| 0.300 | 18 | 161 | 17 | 162 |
| 0.325 | 18 | 158 | 17 | 162 |
| **0.350** | **18** | **147** | 17 | 162 |
| 0.375 | 18 | 133 | 17 | 162 |
| 0.400 | 17 | 119 | 17 | 162 |
| 0.425 | 17 | 93 | 17 | 162 |
| 0.450 | 17 | 74 | 17 | 162 |
| 0.475 | 17 | 48 | 17 | 162 |
| 0.500 | 17 | 36 | **17** | **158** |
| 0.525 | 17 | 22 | 17 | 150 |
| 0.550 | 16 | 15 | 17 | 139 |
| 0.575 | 16 | 11 | 17 | 114 |
| 0.600 | 13 | 7 | 17 | 92 |

bge-small's one miss at every threshold is the top-5 cut (the German memory-limit record ranked sixth for an English query), not the threshold.

Lowest score of the right record and highest score of another record, per group:

| Group | Queries | bge-m3: lowest right | bge-m3: highest other | bge-small: lowest right | bge-small: highest other |
|---|---|---|---|---|---|
| Gather's query | 6 | **0.387** | 0.638 | 0.643 | 0.816 |
| same language | 5 | 0.606 | 0.575 | 0.733 | 0.701 |
| other language | 7 | 0.547 | 0.634 | 0.636 | 0.756 |

At 0.35, bge-m3's recall per group: Gather 6 of 6, same language 5 of 5, other language 7 of 7. The record that 0.5 dropped, D2 (`Speicher erschöpft bei b/compose:wiki`) for `wiki: oom`, comes first, as the same service's record.

Raw data, one JSON line per query with every record's score: [`m3`](data/embeddings-min-score-m3-recall.jsonl), [`small`](data/embeddings-min-score-small-recall.jsonl).

## What it shows

- **Gather's query is the one that scores low.** `"<service>: <signal kinds>"` is a few words, and bge-m3 scores it against a German record at 0.39; every free-text query scored its right record 0.547 or more. The threshold has to fit the shortest query Bleep asks.
- **One threshold does not fit both models.** bge-m3 spreads its scores lower and wider (other records 0.30 to 0.64) than bge-small (0.48 to 0.82), so 0.5 cuts bge-m3's right answers and filters almost nothing for bge-small. The threshold belongs to the model, which is why it is set per model.
- **At 0.35 the threshold is a floor, not the filter,** as it is for bge-small at 0.5: it drops 15 of 162 unrelated records, and what keeps Gather's three short is `k` and the same-service order.

## Limits

- **18 queries over 10 records, written by one person.** The threshold rests on one score at its edge (0.387); another short query or another language may score lower. One step of headroom is a judgement, not a measurement.
- **bge-m3 through an endpoint was not measured.** Ollama serves its own build of bge-m3, so its scores may differ a little from BAAI's ONNX export; Bleep applies 0.35 to any model named `bge-m3` (with or without an owner or a tag), and `EMBEDDINGS_MIN_SCORE` overrides it if recall misses.
- **Only bge-small and bge-m3 have a measured threshold.** Any other model keeps 0.5 unless `EMBEDDINGS_MIN_SCORE` is set; running `recall.py` with that model gives the sweep to choose from.

## Reproduce

```bash
cd backend
MODEL=small uv run python ../docs/spikes/embeddings/recall.py
FASTEMBED_CACHE_PATH=/some/cache MODEL=m3 uv run python ../docs/spikes/embeddings/recall.py
cd ..
docker build -f docs/spikes/embeddings/m3.Dockerfile --build-context spike=docs/spikes/embeddings \
    -t bleep-spike-306:m3 backend
for t in 0.5 0.35; do
  docker run --rm --network none -e MODEL=m3 -e MIN_SCORE=$t \
      -v "$PWD/docs/spikes/embeddings:/bench:ro" --entrypoint python bleep-spike-306:m3 /bench/recall.py
done
docker image rm bleep-spike-306:m3
```
