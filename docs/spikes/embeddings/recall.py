"""Memory recall on past incidents written in English and in German (#146): ten stored records in
MemoryStore's form, searched with Gather's query and with free-text recall_incidents queries in
both languages. For each query: the expected record's rank and score, the best other record's
score, how many other records pass recall's `min_score` (MIN_SCORE, 0.5 by default), and where
the record lands in Bleep's own recall: Gather keeps the top 3 over it with the same service's
first (`MemoryStore.similar`), recall_incidents the top 5 over it.

Then a sweep (#306): for each threshold from 0.30 to 0.60, how many right records Bleep's recall
returns and how many records that are not the answer pass it, to pick a model's `min_score`.

    MODEL=small|m3 [MIN_SCORE=0.5] python docs/spikes/embeddings/recall.py   (backend's venv or
    the image)
"""

import json
import os
import sys

import numpy as np
from models import embedder

MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.5"))
SWEEP = [round(0.30 + 0.025 * i, 3) for i in range(13)]
GATHER_K, TOOL_K = 3, 5

# What MemoryStore embeds: "<title>. Cause: <cause> Action: <action>." The title, cause and
# action are the report's, written by the model, here in English for E* and in German for D*.
STORED = {
    "E1": "container died on a/compose:shop. Cause: shop-web was stopped and its restart policy "
    "is no, so nothing brought it back. Action: docker.start shop-web-1 on a: done.",
    "E2": "url failed on a/compose:shop. Cause: the proxy routes /shop to the wrong upstream port "
    "after the last deploy. Action: nothing ran.",
    "E3": "disk on a. Cause: the Docker build cache filled /var/lib/docker to 95 percent. "
    "Action: nothing ran.",
    "E4": "oom on a/compose:blog. Cause: the blog worker leaks memory and hit its 512 MiB limit. "
    "Action: docker.restart blog-worker-1 on a: done.",
    "E5": "container died on b/compose:mail. Cause: postfix exited after a config syntax error in "
    "main.cf. Action: nothing ran.",
    "D1": "shop-web auf a ungesund. Cause: Die Datenbank shop-db nahm keine Verbindungen mehr an, "
    "weil ihr Volume voll war, deshalb schlug der Health Check von shop-web fehl. "
    "Action: docker.restart shop-db-1 auf a: erledigt.",
    "D2": "Speicher erschöpft bei b/compose:wiki. Cause: Der Wiki-Container hat beim Erzeugen der "
    "Vorschaubilder sein Speicherlimit von 1 GiB überschritten und wurde vom Kernel beendet. "
    "Action: docker.restart wiki-app-1 auf b: erledigt.",
    "D3": "URL von b/compose:cloud nicht erreichbar. Cause: Das TLS-Zertifikat des Reverse Proxy "
    "war abgelaufen, die Seite antwortete mit einem Zertifikatsfehler. Action: nichts ausgeführt.",
    "D4": "Festplatte voll auf b. Cause: Die json-file-Logs des Containers grafana wuchsen ohne "
    "Rotation auf 40 GB und füllten die Platte. Action: nichts ausgeführt.",
    "D5": "Container beendet auf a/compose:worker. Cause: Der Worker beendete sich mit "
    "Exit-Code 1, weil die Umgebungsvariable REDIS_URL nach dem letzten Deploy fehlte. "
    "Action: docker.start shop-worker-1 auf a: erledigt.",
}

SERVICE = {
    "E1": "shop", "E2": "shop", "E3": None, "E4": "blog", "E5": "mail",
    "D1": "shop", "D2": "wiki", "D3": "cloud", "D4": None, "D5": "worker",
}  # fmt: skip

# (group, query, expected record). Gather asks with "<service or host>: <signal kinds>", which is
# the same in every language; recall_incidents takes the model's own words.
QUERIES = [
    ("gather", "shop: container_died", "E1"),
    ("gather", "shop: url_failed", "E2"),
    ("gather", "blog: oom", "E4"),
    ("gather", "wiki: oom", "D2"),
    ("gather", "cloud: url_failed", "D3"),
    ("gather", "worker: container_died", "D5"),
    ("same language", "proxy sends requests to the wrong upstream port", "E2"),
    ("same language", "postfix stopped because of a syntax error in its config", "E5"),
    ("same language", "Datenbank nimmt keine Verbindungen an, weil das Volume voll ist", "D1"),
    ("same language", "Logs ohne Rotation haben die Festplatte gefüllt", "D4"),
    ("same language", "Umgebungsvariable fehlt nach dem Deploy, Worker startet nicht", "D5"),
    ("cross language", "container killed after it went over its memory limit", "D2"),
    ("cross language", "expired TLS certificate on the reverse proxy", "D3"),
    ("cross language", "container logs without rotation filled the disk", "D4"),
    ("cross language", "worker exits with code 1, an environment variable is missing", "D5"),
    ("cross language", "Container wurde gestoppt und die Restart-Policy startet ihn nicht", "E1"),
    ("cross language", "Der Build-Cache hat /var/lib/docker gefüllt", "E3"),
    ("cross language", "Speicherleck im Blog-Worker, Speicherlimit erreicht", "E4"),
]


def bleep_position(
    group: str, query: str, row: np.ndarray, ids: list[str], want: int, min_score: float
) -> str:
    """The record's place in what Bleep's recall returns, or why it is not there."""
    if row[want] < min_score:
        return f"dropped (< {min_score})"
    kept = [i for i in range(len(ids)) if row[i] >= min_score]
    if group == "gather":
        service, k = query.split(":")[0], GATHER_K
        kept.sort(key=lambda i: (SERVICE[ids[i]] != service, -row[i]))
    else:
        k = TOOL_K
        kept.sort(key=lambda i: -row[i])
    place = kept.index(want) + 1
    return str(place) if place <= k else f"cut ({place} > k={k})"


def sweep(scores: np.ndarray, ids: list[str]) -> None:
    """Right records returned and wrong records passing, per threshold."""
    right = [float(row[ids.index(e)]) for (_, _, e), row in zip(QUERIES, scores, strict=True)]
    wrong = [
        float(row[i])
        for (_, _, e), row in zip(QUERIES, scores, strict=True)
        for i in range(len(ids))
        if ids[i] != e
    ]
    print(f"right records: lowest {min(right):.3f}, mean {np.mean(right):.3f}")
    print(f"other records: highest {max(wrong):.3f}, mean {np.mean(wrong):.3f}")
    print("| min_score | Right returned (of 18) | Right dropped | Others passing (of 162) |")
    print("|---|---|---|---|")
    for t in SWEEP:
        returned = sum(
            bleep_position(g, q, row, ids, ids.index(e), t).isdigit()
            for (g, q, e), row in zip(QUERIES, scores, strict=True)
        )
        dropped = sum(s < t for s in right)
        print(f"| {t:.3f} | {returned} | {dropped} | {sum(s >= t for s in wrong)} |")


def main() -> None:
    model = embedder()
    ids = list(STORED)
    scores = model.embed([q for _, q, _ in QUERIES]) @ model.embed(list(STORED.values())).T
    rows = []
    for (group, query, expected), row in zip(QUERIES, scores, strict=True):
        order = list(np.argsort(-row))
        want = ids.index(expected)
        others = [float(row[i]) for i in range(len(ids)) if i != want]
        masked = row.copy()
        masked[want] = -np.inf
        rows.append(
            {
                "group": group,
                "query": query,
                "expected": expected,
                "rank": order.index(want) + 1,
                "score": round(float(row[want]), 3),
                "best_other": round(max(others), 3),
                "best_other_id": ids[int(np.argmax(masked))],
                "others_over_min": sum(s >= MIN_SCORE for s in others),
                "in_bleep": bleep_position(group, query, row, ids, want, MIN_SCORE),
                "scores": {ids[i]: round(float(row[i]), 3) for i in range(len(ids))},
            }
        )
    print(f"model {model.model}, min_score {MIN_SCORE}")
    print(
        f"| Group | Query | Expected | Rank | Score | Best other | Others >= {MIN_SCORE}"
        " | In Bleep |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['group']} | {r['query']} | {r['expected']} | {r['rank']} | {r['score']:.2f}"
            f" | {r['best_other']:.2f} ({r['best_other_id']}) | {r['others_over_min']}"
            f" | {r['in_bleep']} |"
        )
    print()
    print("| Group | Queries | hit@1 | hit@3 | Mean margin | Returned by Bleep's recall |")
    print("|---|---|---|---|---|---|")
    for group in ["gather", "same language", "cross language", None]:
        part = [r for r in rows if group is None or r["group"] == group]
        hit1 = sum(r["rank"] == 1 for r in part)
        hit3 = sum(r["rank"] <= 3 for r in part)
        margin = sum(r["score"] - r["best_other"] for r in part) / len(part)
        returned = sum(r["in_bleep"].isdigit() for r in part)
        name = group or "all"
        print(f"| {name} | {len(part)} | {hit1} | {hit3} | {margin:+.2f} | {returned} |")
    print()
    sweep(scores, ids)
    if out := os.environ.get("OUT"):
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nraw: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
