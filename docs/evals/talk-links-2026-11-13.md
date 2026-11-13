# The chat names a watched URL, 2026-11-13

> **Answer:** `prompts/talk/read.md` now tells the model it may write a URL that one of the incident's checks names as its `target` (#543); code still takes out every link but those to a URL Bleep watches. With that sentence, `openai/gpt-6-luna` (the cheap role) wrote `https://shop.example.com/` in both answers to "how did you check it works again?" after the shop fix, and code kept it: no answer in the round showed "(link removed)". The other talk cases, which share `read.md`, held: 41 of 42 runs passed. The one failure, `de-followup-turns`, answered a German follow-up in English, as it has in earlier rounds. The round cost $0.0081.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`, `cheap` role) |
| Judge | none, scored in code (`backend/evals/talk.py`) |
| Cases | all 42 talk cases, once each; the `checked` state now carries `watched_urls: ["https://shop.example.com/"]`, the URL its `verify_url` check ran |
| When | 2026-11-13 12:17 to 12:18 UTC |
| Code | `feat/543-keep-watched-url-links` on `9ba5b64c` plus the `read.md` sentence and the state's `watched_urls` |
| Raw data | [`talk-20261113-121701.jsonl`](data/talk-20261113-121701.jsonl) |
| Reproduce | `uv run --group evals python -m evals.talk` (in `backend/`) |
| Cost | $0.0081 by the runs' own `usd` (129,442 tokens) |

## Results

| Language | Runs | Passed |
|---|---|---|
| en | 22 | 22 of 22 |
| de | 20 | 19 of 20 |

- **The URL in the answer:** "…and checked that https://shop.example.com/ answered 200." (en) and "Außerdem habe ich geprüft, dass https://shop.example.com/ mit HTTP 200 antwortet." (de). On 2026-11-10, before #543, the same cases showed "(link removed)" there ([raw data](data/talk-20261110-162706.jsonl)).
- **Look cases:** neither answer about the look at dkwebsite wrote a URL; that look ran no URL check.
- **`de-how-checked-fix`:** first scored as failed; the answer was right ("shop-db-1 als laufend … geprüft") but said `laufend`, which the case's word list lacked. The list now takes `laufend`; scored again with `--render` on the same replies, it passes.
- **`de-followup-turns`:** "M1 is reachable and healthy. …", in English to "und was ist mit M1?". It failed the same way in earlier rounds (1 of 5 there); nothing in this change touches the language rule.

## Limits

- One run a case: the round shows the URL comes through and nothing else broke, not how often a model leaves it out.
- The planted-link attack is not a case of its own; code removes such a link whatever the model writes (`backend/tests/talk/test_conversation.py`).
