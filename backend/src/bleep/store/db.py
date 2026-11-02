"""SQLite access and numbered migrations."""

import asyncio
import contextlib
import re
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import aiosqlite

PACKAGED_MIGRATIONS = resources.files("bleep.store") / "migrations"
MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    """A migration file is badly named, duplicated or fails to apply."""


async def _close_when_open(opening: asyncio.Future[aiosqlite.Connection]) -> None:
    with contextlib.suppress(Exception):  # if the connect itself failed there is nothing to close
        await (await opening).close()


class Database:
    def __init__(self, path: Path, migrations: Traversable | Path | None = None) -> None:
        self.path = path
        self.migrations = migrations if migrations is not None else PACKAGED_MIGRATIONS

    async def connect(self) -> aiosqlite.Connection:
        """Open a connection. If the caller is cancelled midway, the connection is closed, not
        left running on its own thread: the watcher is cancelled while it writes on shutdown."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        opening = asyncio.ensure_future(aiosqlite.connect(self.path))
        try:
            conn = await asyncio.shield(opening)
        except asyncio.CancelledError:
            await _close_when_open(opening)
            raise
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
        except BaseException:
            await conn.close()
            raise
        return conn

    def _migration_files(self) -> list[tuple[int, Traversable | Path]]:
        found: dict[int, Traversable | Path] = {}
        for file in self.migrations.iterdir():
            if not file.name.endswith(".sql"):
                continue
            match = MIGRATION_NAME.match(file.name)
            if match is None:
                raise MigrationError(f"{file.name}: migration files are named NNNN_name.sql")
            version = int(match.group(1))
            if version in found:
                raise MigrationError(
                    f"{file.name}: number {version:04d} is also used by {found[version].name}"
                )
            found[version] = file
        return sorted(found.items())

    async def migrate(self) -> None:
        """Apply each migration once, in order; each file and its version row commit together."""
        files = self._migration_files()
        conn = await self.connect()
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            async with conn.execute("SELECT version FROM schema_version") as cursor:
                applied = {row["version"] for row in await cursor.fetchall()}
            for version, file in files:
                if version in applied:
                    continue
                record = (
                    "INSERT INTO schema_version (version, name, applied_at) "
                    f"VALUES ({version}, '{file.name}', '{datetime.now(UTC).isoformat()}')"
                )
                script = f"BEGIN;\n{file.read_text(encoding='utf-8')}\n;\n{record};\nCOMMIT;"
                try:
                    await conn.executescript(script)
                except Exception as error:
                    await conn.rollback()
                    raise MigrationError(f"{file.name}: {error}") from error
        finally:
            await conn.close()
