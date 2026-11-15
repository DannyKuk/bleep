"""Staged history for the demo video's throwaway Bleep (docs/demo.md "The README video").

Past incidents over the last three weeks with votes and model usage, and a few recoveries, so the
dashboard looks as if Bleep has been in use for a while. Only the incident filmed afterwards is a
real run; this history is made up. It writes nothing to long-term memory, so the filmed run's
investigation cannot recall any of it, and every incident is older than the 24 h fold window, so
none of them makes the filmed one a repeat.

Run inside the throwaway's backend container, never the live one:
    docker exec -i bleep-video-backend-1 python - < demo/video/seed.py
"""

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

from bleep.incidents.repository import new_incident_id

if os.environ.get("BLEEP_HOSTS_FILE") != "/video/hosts.video.yaml":
    sys.exit("refusing: this is not the video's throwaway Bleep")

HOST = "web-01"
MODEL = "openai/gpt-6-luna"
NOW = datetime.now(UTC).replace(microsecond=0)

# (days ago, service, title kind, signal kind, container, url detail, status, minutes open,
#  votes {target: vote}, usage calls as (tokens_in, tokens_out, usd))
PAST = [
    (
        19.3,
        "edge-proxy",
        "url failed",
        "url_failed",
        None,
        {"url": "http://web-01:8081/", "status": 502, "error": "expected status 200, got 502"},
        "resolved",
        6,
        {"diagnosis": "up", "proposal": "up"},
        [
            (11840, 190, 0.00152),
            (9120, 240, 0.00121),
            (14260, 310, 0.00187),
            (2650, 160, 0.00041),
            (3110, 120, 0.00044),
            (880, 100, 0.00014),
        ],
    ),
    (
        15.1,
        "shop",
        "oom",
        "oom",
        "shop-worker",
        None,
        "closed",
        41,
        {"diagnosis": "up"},
        [
            (12960, 210, 0.00168),
            (10420, 280, 0.00139),
            (16880, 330, 0.00219),
            (3020, 290, 0.00052),
            (940, 110, 0.00015),
        ],
    ),
    (
        11.4,
        "docs-site",
        "container died",
        "container_died",
        "docs-site",
        None,
        "resolved",
        4,
        {"diagnosis": "up", "proposal": "down"},
        [
            (8760, 170, 0.00113),
            (7240, 220, 0.00097),
            (2480, 150, 0.00038),
            (2900, 120, 0.00042),
            (810, 90, 0.00013),
        ],
    ),
    (
        8.2,
        "shop",
        "unhealthy",
        "unhealthy",
        "shop-web",
        None,
        "resolved",
        7,
        {"diagnosis": "up", "proposal": "up"},
        [
            (13410, 230, 0.00176),
            (11950, 260, 0.00157),
            (18320, 350, 0.00238),
            (2870, 170, 0.00045),
            (3260, 130, 0.00047),
            (960, 110, 0.00015),
        ],
    ),
    (
        4.9,
        "shop",
        "url failed",
        "url_failed",
        None,
        {
            "url": "http://web-01:8080/api/items",
            "status": 500,
            "error": "expected status 200, got 500",
        },
        "closed",
        23,
        {"diagnosis": "down"},
        [
            (14120, 240, 0.00184),
            (12630, 310, 0.00168),
            (17540, 360, 0.00230),
            (3190, 300, 0.00055),
            (990, 120, 0.00016),
        ],
    ),
    (
        1.8,
        "edge-proxy",
        "container died",
        "container_died",
        "edge-proxy",
        None,
        "resolved",
        3,
        {"proposal": "up"},
        [
            (9020, 180, 0.00117),
            (7660, 210, 0.00101),
            (2590, 150, 0.00040),
            (2980, 110, 0.00042),
            (850, 90, 0.00013),
        ],
    ),
]

OOM_BACK = "It was killed for memory while loading a batch and came back within its grace window."
RELOAD_BACK = "It exited during a config reload and its restart policy brought it back."
RECOVERIES = [
    (17.6, "shop", "shop-worker", OOM_BACK),
    (9.7, "edge-proxy", "edge-proxy", RELOAD_BACK),
    (2.3, "shop", "shop-worker", OOM_BACK),
]

DEATHS = {"container_died", "oom"}


def iso(at: datetime) -> str:
    return at.isoformat()


def fold_key(subject_key: str, signal: str, container: str | None, detail: dict | None) -> str:
    if signal == "url_failed":
        assert detail is not None
        return f"{subject_key}|url_failed|{detail['url']}|{detail['status']}"
    kind = "death" if signal in DEATHS else signal
    return f"{subject_key}|{kind}|{container or ''}"


def seed(db: sqlite3.Connection) -> None:
    if db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]:
        sys.exit("refusing: the throwaway already has incidents")
    counts: dict[str, int] = {}
    for days, service, title_kind, signal, container, detail, status, minutes, votes, calls in PAST:
        opened = NOW - timedelta(days=days)
        closed = opened + timedelta(minutes=minutes)
        service_id = f"{HOST}/compose:{service}"
        subject = {"kind": "service", "host_id": HOST, "service_id": service_id}
        subject_key = f"service:{HOST}:{service_id}"
        counts[subject_key] = counts.get(subject_key, 0) + 1
        incident_id = new_incident_id(opened)
        db.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at, closed_at, fold_key, short_name)"
            " VALUES (?, 'service', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                incident_id,
                HOST,
                service_id,
                subject_key,
                status,
                f"{title_kind} on {service_id}",
                iso(opened),
                iso(closed),
                fold_key(subject_key, signal, container, detail),
                f"{service} #{counts[subject_key]}",
            ),
        )
        row = {
            "subject": subject,
            "kind": signal,
            "at": iso(opened).replace("+00:00", "Z"),
            "detail": {"ok": False, "latency_ms": 41, **detail} if detail else {},
            "container": container,
            "container_id": None,
        }
        db.execute(
            "INSERT INTO incident_signals (incident_id, signal, at) VALUES (?, ?, ?)",
            (incident_id, json.dumps(row), iso(opened)),
        )
        for target, vote in votes.items():
            db.execute(
                "INSERT INTO incident_feedback (incident_id, target, vote, note, at)"
                " VALUES (?, ?, ?, NULL, ?)",
                (incident_id, target, vote, iso(closed)),
            )
        for i, (tokens_in, tokens_out, usd) in enumerate(calls):
            at = opened + timedelta(seconds=20 + 25 * i)
            db.execute(
                "INSERT INTO usage (incident_id, model, tokens_in, tokens_out, usd, day,"
                " created_at, cache_read_tokens, cache_write_tokens, provider)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'openrouter')",
                (
                    incident_id,
                    MODEL,
                    tokens_in,
                    tokens_out,
                    usd,
                    at.date().isoformat(),
                    at.replace(tzinfo=None).isoformat(),
                    int(tokens_in * 0.8) if i else 0,
                ),
            )
    for days, service, container, explanation in RECOVERIES:
        db.execute(
            "INSERT INTO recovery_events (host_id, service_id, container, at, explanation)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                HOST,
                f"{HOST}/compose:{service}",
                container,
                iso(NOW - timedelta(days=days)),
                explanation,
            ),
        )


with sqlite3.connect("/data/bleep.db") as conn:
    seed(conn)
print(f"seeded {len(PAST)} incidents and {len(RECOVERIES)} recoveries")
