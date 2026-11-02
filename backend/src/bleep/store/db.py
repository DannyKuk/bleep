"""SQLite access and numbered migrations."""

from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import aiosqlite

MIGRATIONS = resources.files("bleep.store") / "migrations"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        return conn

    async def migrate(self) -> None:
        """Apply each migrations/NNNN_*.sql once, in filename order."""
        files = sorted(
            (f for f in MIGRATIONS.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name
        )
        conn = await self.connect()
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            async with conn.execute("SELECT version FROM schema_version") as cursor:
                applied = {row["version"] for row in await cursor.fetchall()}
            for file in files:
                version = int(file.name.split("_", 1)[0])
                if version in applied:
                    continue
                await conn.executescript(file.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, file.name, datetime.now(UTC).isoformat()),
                )
                await conn.commit()
        finally:
            await conn.close()
