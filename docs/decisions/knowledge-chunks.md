# Knowledge chunks: 300 tokens, cut at headings, one hit per page

> **Answer:** knowledge search cuts every page at its headings first, then cuts a section longer than **300 estimated tokens** at line boundaries (at the end of a paragraph when one falls in the second half), with **50 tokens of overlap**, and embeds each chunk with the page title and the headings above it. On 20 hand-written questions over the 27-page Docker docs snapshot, this put the right page first for 20 of 20, where 400 and 600 tokens put it first for 18 of 20 (and in the top 5 for all 20 at every size). At 300 the largest chunk is 398 of the model's 512 tokens, so the model never cuts one. Search returns the best chunk of each of the 5 closest pages. The embedder is the one memory uses ([embeddings](embeddings.md)).

## Question

The plan suggested chunks of 400 tokens with 50 of overlap. bge-small reads at most 512 tokens and cuts the rest, the tool's answer is capped at 8,000 characters for 5 hits, and Docker's pages are long references with many short sections. How big should a chunk be, how is the size counted without loading the model, and what does a search return?

## What we tried

- **Chunk limits of 200, 300, 400 and 600 estimated tokens**, with 50 of overlap, and 200 and 400 without overlap, on the [retrieval check](../evals/knowledge-2026-11-03.md): 20 questions, each with its expected page, hit@1, hit@3, hit@5 and MRR over the top 5 pages.
- **Two token estimates**, checked against the model's own tokenizer over the whole snapshot: one token per word or mark, and the same with digits in threes and underscores as their own token.
- **Cutting at headings**, so a chunk never spans two sections, with the heading path (`Define services in Docker Compose > healthcheck`) in the text that is embedded.

## Result

| Chunk limit | Overlap | Chunks | Ingest | hit@1 | hit@5 | Largest chunk (model tokens) |
|---|---|---|---|---|---|---|
| 200 | 50 | 595 | 17.4 s | 100% | 100% | |
| **300** | **50** | **444** | **19.8 s** | **100%** | **100%** | **398** |
| 400 | 50 | 378 | 22.4 s | 90% | 100% | 518 |
| 600 | 50 | 341 | 22.1 s | 90% | 100% | |

Overlap made no difference at 200 and a small one at 400 (95% hit@1 without, 90% with). With one token per word or mark, chunks cut at 400 estimated tokens ran up to 862 model tokens and 7 of 369 went past 512, all of them timestamps, hashes or `snake_case` metric names. Counting digits in threes and underscores apart brought the median ratio of model tokens to estimated ones to 1.17 and the largest chunk at 400 to 518.

## What it shows

- **A chunk that is about one thing ranks its page first.** The two questions that 400 and 600 tokens ranked second (the on-failure policy's ten-second rule, the OOM killer) lost first place to a page that mentions the subject in passing inside a longer chunk.
- **300 is the largest limit that kept first place on every question**, and it leaves room under the model's 512 even for number-heavy text. 200 did as well on these questions but gives the model less of each section per hit.
- **Five hits of 300 tokens fit the tool's 8,000 characters.** Each hit gets an equal share (1,560 characters), so the closest one is never the one cut.

## Decided along the way

- **One hit per page.** Search scores every chunk, keeps each page's best, and returns the 5 best pages. Reason: a long reference page (Compose `services`) would otherwise fill all 5 slots with its own sections.
- **The page title and headings are embedded with each chunk, and returned with it.** Reason: "`start_period`" alone says nothing about health checks; under "Dockerfile reference > HEALTHCHECK" it does, and the model sees where the text is from.
- **Table padding is squeezed out before counting.** Docker's tables are padded to align columns; the restart policy table was mostly spaces.
- **The chunk settings and a chunking version are part of the content hash**, so changing either re-embeds every page on the next start, and leaving them unchanged embeds nothing. `CHUNK_VERSION` goes up whenever the cutting or the token estimate changes (#39 review).
- **A single word longer than the limit is cut by characters.** A long URL, base64 or minified JSON in a repo's docs would otherwise make one chunk far over the limit; no word in the snapshot is that long, so its 444 chunks are unchanged (#39 review).

## Limits

- **20 questions over one corpus, written by the person who chose the pages.** The differences between sizes come down to 2 questions; they agree in direction with the reason above, but they are not a benchmark.
- **The estimate is still an estimate.** Text far from Docker's docs (base64, long URLs in a repo's README) can run past 512 model tokens at 300 estimated ones; the model then reads the first 512.
- **Repo docs and past incidents are chunked the same way but were not measured.**

## Reproduce

```bash
cd backend
uv run python -m evals.knowledge --max-tokens 300                     # Bleep's settings
uv run python -m evals.knowledge --max-tokens 400 --label chunks-400   # and 200, 600; --overlap 0
```

The token counts come from [`docs/spikes/knowledge-chunks/token_counts.py`](../spikes/knowledge-chunks/token_counts.py), which chunks the snapshot and counts each chunk, with its title and headings, using the model's own tokenizer; its output on this snapshot is in [`token_counts.txt`](../spikes/knowledge-chunks/token_counts.txt):

```bash
cd backend
uv run python ../docs/spikes/knowledge-chunks/token_counts.py --max-tokens 300
uv run python ../docs/spikes/knowledge-chunks/token_counts.py --max-tokens 400 --first-estimate
```
