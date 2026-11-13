# Prompt caching on a real incident on Claude, 2026-11-13

> **Answer:** caching works on the live Anthropic API (#529, code in #542): in a look at molecula on Claude Sonnet 5.5, every investigate call after the first read the earlier prefix back from the cache, and the second investigate round read the whole first round (25,847 and 26,998 tokens read). The run cost $0.333. Its own token counts at the plain input price come to $0.412, so caching saved $0.079 (19 %). It saved less than the $0.12 that #529 hoped for, for two reasons. First, the one-shot calls (diagnose, propose) pay the 25 % write premium and never read their entry back ($0.035 of the run). Second, each diagnose made two calls, the tool attempt and then the JSON fallback (#515), and the fallback sends a different prefix, so it misses the cache too.

## Setup

| | |
|---|---|
| What | A look requested in the dashboard chat: "Can you take a look at molecula on the coolify server?", confirmed with Yes |
| Model | `claude-sonnet-5-5` (provider `anthropic`), cheap role `claude-haiku-5-5`, set on the settings page for this run and set back to OpenRouter afterwards |
| When | 2026-11-13 12:43:00 to 12:44:07 UTC, incident `inc_01M7AEXGVRHBERN1X1` (molecula, Coolify host) |
| Code | `main` at `326a91d9` (#542 merged), backend and frontend images rebuilt before the run |
| Raw data | the `usage` rows of that incident in the live database, listed below; the graph steps from its `incident_events` |
| Before | the look at molecula on 2026-11-12 on the same model, before #542: incident `inc_01M78ST0QHZGRXZPRY` |
| Cost | $0.333 by the ledger (one model run; the chat's own confirm used no model call) |

## Results

Each model call of the run, in order. `In` is every input token. `Read` and `Written` are the parts of it the API reported as cache read and cache write. The cost is the ledger's: the plain part at $2/M, reads at 5 % of that, writes at 125 %, output at $10/M.

| # | Step | In | Read | Written | Out | Cost |
|---|---|---|---|---|---|---|
| 1 | investigate, round 1 | 14,947 | 0 | 14,943 | 331 | $0.0407 |
| 2 | investigate, round 1 | 25,849 | 14,943 | 10,904 | 859 | $0.0373 |
| 3 | diagnose, through tools | 15,827 | 0 | 15,823 | 1,398 | $0.0535 |
| 4 | diagnose, JSON fallback | 15,706 | 0 | 15,702 | 1,407 | $0.0533 |
| 5 | investigate, round 2 | 27,002 | 25,847 | 1,151 | 307 | $0.0085 |
| 6 | investigate, round 2 | 28,553 | 26,998 | 1,553 | 648 | $0.0131 |
| 7 | diagnose, through tools | 17,517 | 0 | 17,513 | 1,155 | $0.0553 |
| 8 | diagnose, JSON fallback | 17,396 | 0 | 17,392 | 1,240 | $0.0559 |
| 9 | propose | 3,933 | 0 | 3,929 | 555 | $0.0154 |
| | **Run** | **166,730** | **67,788** | **98,910** | **7,900** | **$0.3331** |

- **The investigate loop reads from the second call on.** Call 2 read call 1's whole prompt, and the second round (calls 5 and 6) read the first round's messages, which it replays unchanged. A read costs $0.10/M instead of $2/M, so calls 5 and 6 cost $0.0085 and $0.0131 for 27–29k tokens each.
- **The one-shot calls never read.** Diagnose and propose send their own prompt once and write it to the cache: 70,359 tokens written that no later call read (calls 3, 4, 7, 8 and 9), $0.035 over what the same calls would cost without caching.
- **Diagnose falls back every time.** Each diagnose made two calls of almost the same size. The first went through `with_structured_output` and gave no tool call. The second (`invoke_structured` in `backend/src/bleep/llm/structured.py`) asked for JSON without the tool, and dropping the tool changes the start of the prompt, so it missed the entry the first call had just written. The backend log shows langchain-anthropic's warning that forced tool calling is not supported on `claude-sonnet-5-5`. This is #515's question, here measured on a real run: 2 of the 3 structured calls fell back.
- **Before and after.** The run without caching would have cost $0.412 at the same prices. That's its own token counts with every input token at $2/M, the same calls with nothing read or written. Caching made it $0.333. The look on 2026-11-12 cost $0.366 for 7 calls and 156,562 input tokens. That run was shorter, because it diagnosed once where this one diagnosed twice, so its total is not a like-for-like comparison. Its calls 4 and 5 show the same diagnose pair (16,067 and 15,946 tokens in).

## What it shows

- Caching is worth it for the investigate loop: a round's later calls cost a tenth of what they did.
- For the one-shot calls it costs money. Leaving `cache_control` off them would have made this run about $0.298, 28 % under the uncached $0.412.
- The JSON fallback costs a whole extra diagnose call each time, about $0.05 on Sonnet, and misses the cache. A structured-output method that Claude answers in one call (#515) would save more than caching the one-shot calls does.

## Limits

- One run, on one service, with one model. The share caching saves grows with the number of investigate calls, so a longer investigation saves more and a short one less.
- The dollar figures are the ledger's, at Anthropic's list prices read on 2026-11-13. They were not checked against Anthropic's billing.
- The cheap role (`claude-haiku-5-5`) made no call in this run, so Haiku's cache rates are untested live.

## Reproduce

Set Settings → Models to `anthropic` / `claude-sonnet-5-5`. Ask Bleep in the chat to look at a service and confirm. Then read the incident's rows:

```sql
select created_at, model, tokens_in, cache_read_tokens, cache_write_tokens, tokens_out, usd
from usage where incident_id = '<incident id>' order by id;
```
