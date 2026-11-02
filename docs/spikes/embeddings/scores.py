"""Cosine scores of the local model between Gather-style queries and stored memory texts, to see
how far apart similar and unrelated incidents land.

    docker run --rm --network none -v "$PWD/docs/spikes/embeddings:/bench:ro" \
        --entrypoint python bleep-backend /bench/scores.py
"""

from bleep.memory.embeddings import FastEmbedEmbedder

# What Gather asks with: "<service or host>: <signal kinds>".
QUERIES = ["shop: container_died", "shop: url_failed", "a: disk", "blog: oom"]
# What MemoryStore embeds: "<title>. Cause: <cause> Action: <action>."
STORED = [
    "container died on a/compose:shop. Cause: shop-web was stopped and its restart policy is no. "
    "Action: docker.start shop-web-1 on a: done.",
    "url failed on a/compose:shop. Cause: the proxy routes /shop to the wrong upstream port. "
    "Action: nothing ran.",
    "disk on a. Cause: the Docker build cache filled /var/lib/docker to 95 percent. "
    "Action: nothing ran.",
    "oom on a/compose:blog. Cause: the blog worker leaks memory and hit its 512 MiB limit. "
    "Action: docker.restart blog-worker-1 on a: done.",
    "container died on b/compose:mail. Cause: postfix exited after a config syntax error. "
    "Action: nothing ran.",
]

embedder = FastEmbedEmbedder()
scores = embedder.embed(QUERIES) @ embedder.embed(STORED).T
print("query".ljust(24) + "".join(f"  #{i + 1} " for i in range(len(STORED))))
for query, row in zip(QUERIES, scores, strict=True):
    print(query.ljust(24) + "".join(f"  {s:.2f}" for s in row))
