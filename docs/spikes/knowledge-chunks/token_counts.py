"""Count each snapshot chunk's tokens with the embedding model's own tokenizer, against the
estimate chunking uses, for one chunk limit. `--first-estimate` counts with the estimate tried
first (a word or a mark each), to show why it was replaced.

    cd backend && uv run python ../docs/spikes/knowledge-chunks/token_counts.py --max-tokens 300
"""

import argparse
import re
import statistics

from fastembed import TextEmbedding

from bleep.knowledge import chunking
from bleep.knowledge.index import HEADING_SEPARATOR, embedded_text
from bleep.knowledge.ingest import load_snapshot

FIRST_ESTIMATE = re.compile(r"\w+|[^\w\s]")
MODEL_LIMIT = 512

parser = argparse.ArgumentParser()
parser.add_argument("--max-tokens", type=int, default=chunking.MAX_TOKENS)
parser.add_argument("--first-estimate", action="store_true")
args = parser.parse_args()
if args.first_estimate:
    chunking._TOKEN = FIRST_ESTIMATE

tokenizer = TextEmbedding("BAAI/bge-small-en-v1.5").model.tokenizer
tokenizer.no_truncation()
real, estimated = [], []
for doc in load_snapshot():
    for chunk in chunking.chunk_markdown(doc.text, args.max_tokens):
        text = embedded_text(doc.title, HEADING_SEPARATOR.join(chunk.heading_path), chunk.text)
        real.append(len(tokenizer.encode(text).ids))
        estimated.append(chunking.estimate_tokens(chunk.text))

ratios = [r / e for r, e in zip(real, estimated, strict=True) if e]
print(
    f"limit {args.max_tokens}, {'first' if args.first_estimate else 'current'} estimate: "
    f"{len(real)} chunks; model tokens median {statistics.median(real):.0f}, max {max(real)}, "
    f"over {MODEL_LIMIT}: {sum(n > MODEL_LIMIT for n in real)}; "
    f"model/estimate median {statistics.median(ratios):.2f}"
)
