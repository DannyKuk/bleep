"""Long-term memory in SQLite: every finished incident's report with its vector, and facts per
service.

Report adds one record per incident (`add_report`, idempotent, so a re-run Report node replaces
it), with the human's feedback on the incident (`set_feedback`, which also updates a record already
written), and up to two facts learned from the human's instructions (`replace_report_facts`).
Gather and the `recall_incidents` tool ask `similar`: the query is embedded, every record of
the current embedder is scored by cosine with numpy (brute force; a few thousand records take
well under a millisecond), records below `min_score` are dropped, and the same service's
records come first. Records whose `model` is not the current embedder's name are left out until
`reembed` (run on boot) embeds them again. Text is cleaned before it is stored (`memory.text`).
See docs/design/memory.md.
"""

import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Literal

import aiosqlite
import numpy as np

from bleep.incidents.repository import utcnow
from bleep.memory.embeddings import Embedder
from bleep.memory.text import NOTE_CHARS, clean, describe_feedback
from bleep.schemas.graph import (
    Decision,
    Feedback,
    FeedbackTarget,
    PastIncident,
    Proposal,
    Report,
    ServiceFact,
    Vote,
)
from bleep.schemas.incidents import Incident
from bleep.store.db import Database

NOTHING_RAN = "nothing ran"
REEMBED_BATCH = 64


def decision_memory(decision: Decision | None, proposal: Proposal | None) -> str | None:
    """The human's decision as memory keeps it, with the plan and the day:
    "rejected docker.restart shop-web-1 on 2026-11-10"."""
    if decision is None:
        return None
    day = f"{decision.at:%Y-%m-%d}"
    steps = proposal.steps if proposal is not None else []
    plan = "; ".join(f"{s.action} {s.target}" for s in steps) or "the plan"
    if decision.kind == "approve":
        return f"approved {plan} on {day}"
    if decision.kind == "reject":
        return f"rejected {plan} on {day}"
    return f"instructed on {day}: {decision.text or ''}"


def _embedded_text(title: str, cause: str, action: str) -> str:
    """What a record is found by: what failed and where, why, and what was done."""
    return f"{title}. Cause: {cause} Action: {action}."


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


class MemoryStore:
    def __init__(
        self, db: Database, embedder: Embedder, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.clock = clock

    async def _embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embedder.embed, texts)

    async def add_report(
        self,
        incident: Incident,
        report: Report,
        decision: Decision | None = None,
        *,
        proposal: Proposal | None = None,
    ) -> None:
        """Remember a finished incident; a second call for the same incident replaces the first.

        `proposal` names the plan the decision was about, so recall can say what was rejected.
        The human's feedback is read from the incident's votes in the same write, so a vote cast
        while the record is written is never lost (`set_feedback` updates the record after it).
        """
        title, cause = clean(report.title), clean(report.cause)
        action = clean(report.action_taken or NOTHING_RAN)
        human = decision_memory(decision, proposal)
        text = _embedded_text(title, cause, action)
        vector = (await self._embed([text]))[0]
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            feedback = describe_feedback(await _votes(conn, incident.id))
            await conn.execute(
                "INSERT OR REPLACE INTO memory_incidents (incident_id, host_id, service_id, date,"
                " title, cause, action, outcome, human_decision, feedback, text, model, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    incident.id,
                    incident.subject.host_id,
                    incident.subject.service_id,
                    incident.opened_at.isoformat(),
                    title,
                    cause,
                    action,
                    clean(report.outcome),
                    clean(human) if human else None,
                    clean(feedback) if feedback else None,
                    text,
                    self.embedder.name,
                    _blob(vector),
                ),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def feedback(self, incident_id: str) -> list[Feedback]:
        """The human's votes on the incident, diagnosis first."""
        conn = await self.db.connect()
        try:
            return await _votes(conn, incident_id)
        finally:
            await conn.close()

    async def set_feedback(
        self, incident_id: str, target: FeedbackTarget, vote: Vote, note: str | None = None
    ) -> Feedback:
        """Keep the human's vote on a finding, replacing an earlier one on the same target, and
        copy the incident's votes into its memory record when it has one."""
        kept = Feedback(
            target=target,
            vote=vote,
            note=clean(note, NOTE_CHARS) if note else None,
            at=self.clock(),
        )
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "INSERT OR REPLACE INTO incident_feedback (incident_id, target, vote, note, at)"
                " VALUES (?, ?, ?, ?, ?)",
                (incident_id, kept.target, kept.vote, kept.note, kept.at.isoformat()),
            )
            feedback = describe_feedback(await _votes(conn, incident_id))
            await conn.execute(
                "UPDATE memory_incidents SET feedback = ? WHERE incident_id = ?",
                (clean(feedback) if feedback else None, incident_id),
            )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return kept

    async def similar(
        self, text: str, service_id: str | None = None, k: int = 3, min_score: float = 0.5
    ) -> list[PastIncident]:
        """At most `k` past incidents scoring at least `min_score`, the same service's first and
        the closest first within each group."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM memory_incidents WHERE model = ?", (self.embedder.name,)
            ) as cursor:
                rows = list(await cursor.fetchall())
        finally:
            await conn.close()
        if not rows or k <= 0:
            return []
        query = (await self._embed([text]))[0]
        matrix = np.stack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
        scores = matrix @ query
        found = [
            (row, float(score))
            for row, score in zip(rows, scores, strict=True)
            if score >= min_score
        ]
        same = service_id is not None
        found.sort(key=lambda pair: (not (same and pair[0]["service_id"] == service_id), -pair[1]))
        return [
            PastIncident(
                incident_id=row["incident_id"],
                date=datetime.fromisoformat(row["date"]),
                title=row["title"],
                cause=row["cause"],
                action=row["action"],
                outcome=row["outcome"],
                human_decision=row["human_decision"],
                feedback=row["feedback"],
                score=round(score, 4),
            )
            for row, score in found[:k]
        ]

    async def reembed(self) -> int:
        """Embed again every record from another model; how many were. Nothing is embedded (and
        a local model is not even loaded) when every record is the current model's."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT incident_id, text FROM memory_incidents WHERE model != ?",
                (self.embedder.name,),
            ) as cursor:
                stale = [(row["incident_id"], row["text"]) for row in await cursor.fetchall()]
            for start in range(0, len(stale), REEMBED_BATCH):
                batch = stale[start : start + REEMBED_BATCH]
                vectors = await self._embed([text for _, text in batch])
                await conn.executemany(
                    "UPDATE memory_incidents SET model = ?, embedding = ? WHERE incident_id = ?",
                    [
                        (self.embedder.name, _blob(vector), incident_id)
                        for (incident_id, _), vector in zip(batch, vectors, strict=True)
                    ],
                )
                await conn.commit()  # a batch at a time: a crash keeps what was done
        finally:
            await conn.close()
        return len(stale)

    async def facts(self, service_id: str) -> list[ServiceFact]:
        """The service's facts, oldest first."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM service_facts WHERE service_id = ? ORDER BY id", (service_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        return [
            ServiceFact(
                id=row["id"],
                text=row["text"],
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
                incident_id=row["incident_id"],
            )
            for row in rows
        ]

    async def add_fact(
        self, service_id: str, text: str, source: Literal["report", "human"] = "human"
    ) -> ServiceFact:
        fact_text, now = clean(text), self.clock()
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "INSERT INTO service_facts (service_id, text, source, created_at)"
                " VALUES (?, ?, ?, ?)",
                (service_id, fact_text, source, now.isoformat()),
            )
            await conn.commit()
            fact_id = cursor.lastrowid
        finally:
            await conn.close()
        if fact_id is None:  # SQLite always gives an id after an insert
            raise RuntimeError("the fact was stored without an id")
        return ServiceFact(id=fact_id, text=fact_text, source=source, created_at=now)

    async def replace_report_facts(
        self, service_id: str, incident_id: str, texts: Iterable[str]
    ) -> list[ServiceFact]:
        """Keep `texts` as the facts the incident's report taught, in place of any it taught
        before (a re-run Report must not double them); the facts kept."""
        now = self.clock()
        cleaned = [fact for fact in (clean(text) for text in texts) if fact]
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                "DELETE FROM service_facts WHERE incident_id = ? AND source = 'report'",
                (incident_id,),
            )
            kept = []
            for text in cleaned:
                cursor = await conn.execute(
                    "INSERT INTO service_facts (service_id, text, source, created_at, incident_id)"
                    " VALUES (?, ?, 'report', ?, ?)",
                    (service_id, text, now.isoformat(), incident_id),
                )
                if cursor.lastrowid is None:  # SQLite always gives an id after an insert
                    raise RuntimeError("the fact was stored without an id")
                kept.append(
                    ServiceFact(
                        id=cursor.lastrowid,
                        text=text,
                        source="report",
                        created_at=now,
                        incident_id=incident_id,
                    )
                )
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return kept

    async def delete_fact(self, fact_id: int, service_id: str | None = None) -> bool:
        """Forget a fact (only if it is `service_id`'s, when given); False when there was none."""
        query, args = "DELETE FROM service_facts WHERE id = ?", [fact_id]
        if service_id is not None:
            query, args = f"{query} AND service_id = ?", [fact_id, service_id]
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(query, args)
            await conn.commit()
            return cursor.rowcount == 1
        finally:
            await conn.close()


async def _votes(conn: aiosqlite.Connection, incident_id: str) -> list[Feedback]:
    async with conn.execute(
        "SELECT * FROM incident_feedback WHERE incident_id = ? ORDER BY target", (incident_id,)
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        Feedback(
            target=row["target"],
            vote=row["vote"],
            note=row["note"],
            at=datetime.fromisoformat(row["at"]),
        )
        for row in rows
    ]
