"""Approvals in SQLite: each plan is put to the human once and decided once.

Telegram and the dashboard both call `resolve`; it is one `UPDATE ... WHERE status = 'pending'`,
so whichever answers first wins and the other gets None. A grant, the proof act tools check, is
issued only for a plan the store holds as approved. See docs/design/graph.md.
"""

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import datetime

import aiosqlite
from pydantic import SecretStr

from bleep.incidents.repository import utcnow
from bleep.schemas.graph import (
    ApprovalGrant,
    ApprovalRecord,
    ApprovalRequest,
    Decision,
    Proposal,
    TargetSnapshot,
)
from bleep.schemas.incidents import TERMINAL_STATUSES
from bleep.store.db import Database

_STATUS = {"approve": "approved", "reject": "rejected", "instruct": "instructed"}


def plan_hash(proposal: Proposal, snapshots: list[TargetSnapshot]) -> str:
    """sha256 of the steps as canonical JSON, each with the container id its target resolved to.

    A recreated container keeps its name but not its id, so the same words make another plan.
    Steps whose target is not a container (Coolify applications) carry `null`.
    """
    resolved = {(s.host_id, s.target): s.container_id for s in snapshots}
    steps = [
        step.model_dump(mode="json") | {"container_id": resolved.get((step.host_id, step.target))}
        for step in proposal.steps
    ]
    canonical = json.dumps(steps, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def grant_token(secret: str, incident_id: str, plan_hash: str) -> str:
    """HMAC-SHA256 keyed with `BLEEP_SECRET` over the incident id followed by the plan hash."""
    message = (incident_id + plan_hash).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _record(row: aiosqlite.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        incident_id=row["incident_id"],
        plan_hash=row["plan_hash"],
        proposal=Proposal.model_validate_json(row["proposal"]),
        snapshots=[TargetSnapshot.model_validate(s) for s in json.loads(row["snapshots"])],
        requested_at=datetime.fromisoformat(row["requested_at"]),
        decision=Decision.model_validate_json(row["decision"]) if row["decision"] else None,
        reminders=row["reminders"],
    )


class ApprovalStore:
    """The `Approvals` the app runs on. `secret` is `BLEEP_SECRET`, which keys the grants."""

    def __init__(
        self, db: Database, secret: SecretStr | str, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.db = db
        self._secret = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        self.clock = clock

    async def request(
        self, incident_id: str, proposal: Proposal, snapshots: list[TargetSnapshot]
    ) -> ApprovalRequest:
        """Store the plan as pending. An incident has at most one pending approval."""
        request = ApprovalRequest(id=secrets.token_hex(8), plan_hash=plan_hash(proposal, snapshots))
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT INTO approvals (id, incident_id, plan_hash, proposal, snapshots, status,"
                " requested_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                (
                    request.id,
                    incident_id,
                    request.plan_hash,
                    proposal.model_dump_json(),
                    json.dumps([s.model_dump(mode="json") for s in snapshots]),
                    self.clock().isoformat(),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return request

    async def resolve(self, incident_id: str, decision: Decision) -> Decision | None:
        """The decision if it won, None if the approval was already decided or never asked."""
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "UPDATE approvals SET status = ?, decision = ?"
                " WHERE incident_id = ? AND status = 'pending'",
                (_STATUS[decision.kind], decision.model_dump_json(), incident_id),
            )
            await conn.commit()
            return decision if cursor.rowcount == 1 else None
        finally:
            await conn.close()

    async def current(self, incident_id: str) -> ApprovalRecord | None:
        """The incident's latest approval, pending or decided; None if none was ever asked."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM approvals WHERE incident_id = ? ORDER BY rowid DESC LIMIT 1",
                (incident_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return _record(row) if row is not None else None

    async def grant(self, incident_id: str) -> ApprovalGrant | None:
        """The grant for the incident's latest plan if, and only if, it was approved."""
        record = await self.current(incident_id)
        if record is None or record.decision is None or record.decision.kind != "approve":
            return None
        token = grant_token(self._secret, incident_id, record.plan_hash)
        return ApprovalGrant(incident_id=incident_id, plan_hash=record.plan_hash, token=token)

    async def pending(self) -> list[ApprovalRecord]:
        """Every pending approval of an incident that is still open, oldest first."""
        finished = ", ".join("?" for _ in TERMINAL_STATUSES)
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT a.* FROM approvals a JOIN incidents i ON i.id = a.incident_id"
                f" WHERE a.status = 'pending' AND i.status NOT IN ({finished})"
                " ORDER BY a.requested_at, a.rowid",
                TERMINAL_STATUSES,
            ) as cursor:
                return [_record(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def mark_reminded(self, approval_id: str, reminders: int) -> bool:
        """Count `reminders` as sent; False if the approval was decided or already counted."""
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "UPDATE approvals SET reminders = ?"
                " WHERE id = ? AND status = 'pending' AND reminders < ?",
                (reminders, approval_id, reminders),
            )
            await conn.commit()
            return cursor.rowcount == 1
        finally:
            await conn.close()
