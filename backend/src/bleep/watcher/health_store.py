"""Each service's health as the watcher last judged it, and since when it has had it.

The manager records a judgement after every poll tick and every round of URL checks; the
service page reads it as "healthy since" (docs/design/watcher.md).
"""

from datetime import datetime

from bleep.schemas.api import Health
from bleep.store.db import Database


class HealthStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, host_id: str, service_id: str, health: Health, at: datetime) -> None:
        """Keep the service's health; `since` moves to `at` only when the health changed.

        `unknown` means no data, so it is not kept: the last known health and its time stay.
        """
        if health == "unknown":
            return
        conn = await self.db.connect()
        try:
            await conn.execute(
                "INSERT INTO service_health (service_id, host_id, health, since)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (service_id) DO UPDATE SET"
                " host_id = excluded.host_id, health = excluded.health, since = excluded.since"
                " WHERE service_health.health != excluded.health",
                (service_id, host_id, health, at.isoformat()),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def current(self, service_id: str) -> tuple[Health, datetime] | None:
        """The stored health and since when; None before the first known judgement."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT health, since FROM service_health WHERE service_id = ?", (service_id,)
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        return row["health"], datetime.fromisoformat(row["since"])

    async def forget_host(self, host_id: str) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute("DELETE FROM service_health WHERE host_id = ?", (host_id,))
            await conn.commit()
        finally:
            await conn.close()
