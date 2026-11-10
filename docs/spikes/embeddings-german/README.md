# Embeddings on German service docs (#377)

The set and the script for the spike in [#377](https://github.com/DannyKuk/bleep/issues/377):
does knowledge search find the right chunk of a service's own docs when they are in German,
mixed or sloppy, and how does `google/gemini-embedding-2` through OpenRouter compare with
bge-small?

## The set

- [`docs/`](docs/) holds five service docs for a small self-hosted stack (a shop, its worker, an
  nginx reverse proxy, a restic backup job and a Nextcloud-like cloud): 31 chunks. They are
  German or mixed German and English, written to be plausible and sloppy on purpose: typos and
  missing umlauts (`fuer`, `haengen`), half sentences, English terms inside German text
  (`restart policy`, `rate limit`, `dead letter`), Denglisch (`job der failt`, `requeue`), TODOs
  and shouting.
- [`queries.yaml`](queries.yaml) has 22 queries, 15 in English (as the model writes them while
  investigating) and 7 in German, each with the id of the one chunk that answers it. A chunk id
  is `<file>#<position>`; `measure.py --list-chunks` prints them.
- The docs and queries were written by an agent that had not seen any embedder's score on
  them, and were not changed after the first run.
- The chunks are cut by Bleep's own `chunk_markdown` and embedded with `embedded_text` through
  `KnowledgeIndex`, as a linked repo's docs are (`repo_documents`, source `repo`, redacted).
  Searches use `KnowledgeIndex.search` with `k=5`, so "top 5" means the best chunk of each of
  the five closest documents, which is what `knowledge_search` returns to the model: with five
  documents, a query that finds its document but the wrong chunk of it is a miss.

## The script

[`measure.py`](measure.py) takes an embedder and reports, for it:

- (a) first hit and top 5 on this set (all, English queries, German queries);
- (b) the same on #146's 20 English and 11 German knowledge questions over the Docker docs
  snapshot (`backend/evals/knowledge/`);
- (c) #306's 18 recall queries over its 10 records ([`../embeddings/recall.py`](../embeddings/recall.py)):
  first and top 5 by rank among the records, and the cut-off calibration: the lowest right
  record's score, the highest unrelated one, how many right ones Bleep's recall returns and how
  many unrelated ones pass at the cut-off in `MIN_SCORES`, and a sweep from 0.30 to 0.95;
- latency per call (first call, one text, a batch of 31), the vector size, and the number of
  texts and characters a full first ingest embeds (the snapshot's 444 chunks plus this set's 31;
  memory is one text per incident, plus one per recall). Tokens are estimated at 4 characters
  each; read the real count off the provider's usage if the price depends on it.

It uses Bleep's own `FastEmbedEmbedder` and `OpenAICompatEmbedder`, so it measures what Bleep
would do. The key is read from the environment variable named by `--key-env` and is never
printed or written. Nothing here calls a chat model.

```bash
# Bleep's default, as Bleep runs it (free, local):
cd backend && uv run python ../docs/spikes/embeddings-german/measure.py local \
    --out ../docs/spikes/embeddings-german/results-local-bge-small.json

# gemini-embedding-2 through OpenRouter (paid; about 600 texts, see the cost line it prints):
cd backend && EMBEDDINGS_API_KEY=... uv run python ../docs/spikes/embeddings-german/measure.py openai \
    --base-url https://openrouter.ai/api/v1 --model google/gemini-embedding-2 \
    --out ../docs/spikes/embeddings-german/results-gemini-embedding-2.json
```

`--min-score` overrides the cut-off for an `openai` model that has no entry in `MIN_SCORES`
(the default is 0.5); the sweep shows where a better one lies.

## Results

[`results-local-bge-small.txt`](results-local-bge-small.txt) (and `.json`, with every query's
top 5) is the local bge-small run. Its numbers for (b) and (c) match #146 and #306 (20 of 20
and 4 of 11 first, 13 of 18 first and 17 returned), which checks the harness against them.
