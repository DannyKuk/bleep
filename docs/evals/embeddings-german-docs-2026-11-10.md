# gemini-embedding-2 against bge-small on German service docs, 2026-11-10

> **Answer:** `google/gemini-embedding-2` through OpenRouter finds German and mixed-language docs far better than bge-small, and it is the one multilingual option measured so far that does not lose in the top 5 on English. On a new set of 22 queries over sloppy German service docs it put the right chunk first for **22 of 22** (bge-small **18**), and in the top 5 for 22 (bge-small 19). On #146's 11 German questions over the Docker docs it was first for **9 of 11** (bge-small 4; bge-m3 8), top 5 for 11. **It loses one English first hit: 19 of 20 first on #146's English questions against bge-small's 20 of 20** (top 5 equal, 20 of 20). Its recall scores separate better (lowest right past incident 0.692, highest unrelated 0.768, where bge-small has 0.636 and 0.816), and 18 of 18 right records come back at a cut-off of **0.65** (bge-small 17 of 18 at 0.5, the 18th is cut by the top 5). It costs 420 ms per embed over the network (bge-small 15 ms in-process), vectors of 3,072 floats (384), and a key. The run cost about $0.02; a full first ingest of the Docker docs and a repo's docs is about 85,000 tokens, about $0.017 (#377).

## Setup

- **Harness**: [`measure.py`](../spikes/embeddings-german/measure.py), which uses Bleep's own `FastEmbedEmbedder`, `OpenAICompatEmbedder`, `KnowledgeIndex` and chunker. The bge-small run is the local default as Bleep runs it (fastembed, two threads); the gemini run is `EMBEDDINGS_PROVIDER=openai` against `https://openrouter.ai/api/v1`, model `google/gemini-embedding-2`, cut-off 0.5 (no entry yet in `MIN_SCORES` when it ran).
- **New set** ([`docs/spikes/embeddings-german/`](../spikes/embeddings-german/README.md)): five German or mixed-language, deliberately sloppy service docs (shop, worker, nginx proxy, restic backup, Nextcloud-like cloud), 31 chunks cut as Bleep cuts a repo's docs, and 22 queries (15 English, 7 German) with one right chunk each. Written by an agent that had not seen any embedder's results, and not changed afterwards. "Top 5" is what `knowledge_search` returns: the best chunk of each of the five closest documents; with five documents, a query that finds its document but the wrong chunk is a miss.
- **#146's sets**: the 20 English and 11 German and mixed-language questions over the Docker docs snapshot (27 pages, 444 chunks, Bleep's chunk settings), and the 18 recall queries over 10 records of [`recall.py`](../spikes/embeddings/recall.py).
- **Run**: 2026-11-10, macOS (Apple M4 Pro) for bge-small; the gemini calls went over the network from the same laptop. One run each: scores of a hosted model can change when the provider changes it. No chat model was called.
- **Raw data**: [bge-small](data/embeddings-german-docs-bge-small.json), [gemini-embedding-2](data/embeddings-german-docs-gemini-embedding-2.json) (every query's top 5 and the full sweep).
- The bge-small run reproduces the numbers of [#146](embeddings-multilingual-2026-11-06.md) and [#306](embeddings-min-score-2026-11-06.md) (20 of 20 and 4 of 11 first, 13 of 18 first and 17 returned), which checks the harness.

## Result

Right chunk (or page, or record) first, and in the top 5:

| Set | Queries | bge-small: first, top 5 | gemini-embedding-2: first, top 5 |
|---|---|---|---|
| New German set, all | 22 | 18, 19 | **22, 22** |
| New set, English queries | 15 | 14, 15 | 15, 15 |
| New set, German queries | 7 | 4, 4 | **7, 7** |
| #146 English knowledge questions | 20 | **20**, 20 | 19, 20 |
| #146 German and mixed knowledge questions | 11 | 4, 11 | **9**, 11 |
| #306 recall queries, record ranked | 18 | 13, 17 | **14, 18** |

bge-small's misses on the new set were the cloud "trusted domains" chunk (second) and three German queries (shop configuration, backup retention, worker shutdown) whose right chunk was not in its top 5. gemini-embedding-2 missed none of the 22.

### Recall's cut-off

| | bge-small | gemini-embedding-2 |
|---|---|---|
| Lowest score of a right record | 0.636 | 0.692 |
| Highest score of an unrelated record | 0.816 | 0.768 |
| Highest threshold (0.025 steps) returning every right record the top 5 allows | 0.625 | 0.675 |
| Right returned at 0.5 | 17 of 18 | 18 of 18 |
| Unrelated records over 0.5 (of 162) | 158 | 161 |
| Cut-off by #306's rule (one step below the highest) | not re-applied; 0.5 is what recall was built and calibrated on (#306) | **0.65** |
| Right returned, unrelated passing at that cut-off | 17, 158 | **18, 50** |

gemini's scores sit higher and closer together than bge-small's (unrelated records 0.5 to 0.77), so 0.5 filters nothing (161 of 162 pass); at 0.65 it drops 112 of the 162 unrelated records and keeps all 18 right ones. Sweep for gemini-embedding-2, right returned and unrelated passing:

| min_score | 0.500 | 0.600 | 0.625 | 0.650 | 0.675 | 0.700 | 0.725 | 0.750 |
|---|---|---|---|---|---|---|---|---|
| Right returned (of 18) | 18 | 18 | 18 | 18 | 18 | 16 | 14 | 8 |
| Unrelated passing (of 162) | 161 | 105 | 75 | 50 | 26 | 15 | 8 | 2 |

### Cost and speed

| | bge-small (local) | gemini-embedding-2 (OpenRouter) |
|---|---|---|
| Vector size | 384 (1.5 KB as float32) | 3,072 (12 KB as float32), 8 times |
| First call (model load or connection) | 168 ms | 664 ms |
| One text, median (max) | 15 ms (20 ms) | 420 ms (483 ms) |
| Batch of 31 | 648 ms | 986 ms |
| Per text in a batch | 21 ms | 32 ms |
| Money | none | $0.20 per million input tokens (OpenRouter's models API, `GET https://openrouter.ai/api/v1/embeddings/models`, `pricing.prompt` 0.0000002 per token, read 2026-11-10) |

- **The run**: 598 texts in 103 calls, about 360,000 characters, about 90,000 tokens (estimated at 4 characters per token, not a tokenizer count); measured key spend for the whole run about **$0.02**.
- **A full first ingest**: the Docker docs snapshot (444 chunks, 331,000 characters) plus the new set's 31 chunks is 475 texts and about 85,000 tokens, about **$0.017** at that rate (85,000 tokens x $0.20 per million). A person's own linked repos (up to 40 files each) come on top, and each past incident is one text plus one for every recall.
- Re-embedding is skipped by content hash, so the cost is paid once per document and per model change.

## What it shows

- **Docs in German, queried in English, are where bge-small fails quietly.** On English queries over German docs it was fine (14 of 15 first); on German queries over the same docs it put the right chunk first for 4 of 7 and in the top 5 for 4, so `knowledge_search` returns nothing useful for them. gemini-embedding-2 did 7 of 7.
- **It does not lose in the top 5 on English, but it loses one first hit.** 19 of 20 first against bge-small's 20 of 20, top 5 equal at 20 of 20. Investigate reads the top 5, so the loss is small in use, but the issue's rule was to report it before switching, and it is reported here. bge-m3 lost four English first hits (16 of 20), and its top 5 was equal too.
- **Recall separates better.** Right records are 0.69 or more and unrelated ones 0.77 or less, so the cut-off does real work at 0.65, which bge-small's never did.
- **The price is time and a service.** Each embed is a network call of about 420 ms where bge-small takes 15 ms, which an incident's few embeds can carry; a re-embed of 1,000 past incidents at 32 ms per text in batches is about 32 s.

## Limits

- **One run each, from one laptop, on small sets written by one agent.** 22 queries over 31 chunks, 18 recall queries over 10 records. The threshold of 0.65 rests on the lowest right score (0.692) and one step of headroom.
- **Token counts are estimates**; the $0.02 is the key's measured spend for the run, the $0.017 is estimated from the 4-characters-per-token rule and the rate above (OpenRouter's models API, `GET https://openrouter.ai/api/v1/embeddings/models`, `pricing.prompt` 0.0000002 per token, read 2026-11-10).
- **"Top 5" on the German set means little**: it has five documents, so a top 5 holds every document and only a wrong chunk of the right one is a miss.
- **Some English queries share distinctive tokens with their chunk** ("repository is already locked", "413"), which favours any model, bge-small included, over a query written as the model would phrase it from a symptom.
- **OpenRouter being unreachable was read from the code, not run live.** `OpenAICompatEmbedder.embed` turns an HTTP error or a timeout into `EmbedderError`, after up to 30 s (`HTTP_TIMEOUT_S`) per failed call. (a) Gather's recall becomes a gap in the incident's context (`graph/nodes/gather.py`, `_recall`). (b) `knowledge_search` and `recall_incidents` answer the model "the tool failed (EmbedderError)" (`_invoke` in `graph/nodes/investigate.py`) and the run goes on. (c) Saving the finished incident to memory is logged and lost (`_remember` in `graph/nodes/report.py`; the embed happens before the database transaction, so nothing is half written), and the report is still sent. (d) Knowledge ingest runs in the background and tries again (`KnowledgeRefresher._safely` and `run` in `knowledge/ingest.py`), and a failed re-embed at boot is logged (`_reembed` in `main.py`), leaving old records out of recall until the next boot.
- **The 8 times larger vectors** were not measured in the database or for search time; 475 chunks of 12 KB are 5.7 MB.
- Translation at ingest was not tried, as the issue's plan drops it unless Gemini disappoints.
- No other hosted multilingual model was run.

## Reproduce

```bash
cd backend && uv run python ../docs/spikes/embeddings-german/measure.py local \
    --out ../docs/spikes/embeddings-german/results-local-bge-small.json
# paid: about 600 texts, about $0.02; the key stays in the environment
cd backend && EMBEDDINGS_API_KEY=... uv run python ../docs/spikes/embeddings-german/measure.py openai \
    --base-url https://openrouter.ai/api/v1 --model google/gemini-embedding-2 \
    --out ../docs/spikes/embeddings-german/results-gemini-embedding-2.json
```
