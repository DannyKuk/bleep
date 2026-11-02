"""Admin sessions in SQLite, and the login rate limit."""

import hashlib
import math
import secrets
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bleep.store.db import Database

SESSION_TTL = timedelta(days=7)
TOUCH_EVERY = timedelta(minutes=1)
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SessionCheck:
    refreshed: bool  # the expiry slid forward, so the cookie should be renewed too


class SessionStore:
    def __init__(self, db: Database, clock: Callable[[], datetime] = _utcnow) -> None:
        self.db = db
        self.clock = clock

    async def create(self) -> str:
        """Start a session and return its token; only the token's hash is stored."""
        token = secrets.token_urlsafe(32)
        now = self.clock()
        conn = await self.db.connect()
        try:
            await conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
            await conn.execute(
                "INSERT INTO sessions (token_hash, created_at, expires_at, last_seen_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    hash_token(token),
                    now.isoformat(),
                    (now + SESSION_TTL).isoformat(),
                    now.isoformat(),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        return token

    async def check(self, token: str) -> SessionCheck | None:
        """None when the token is unknown or expired; otherwise slide the expiry (at most
        once a minute)."""
        digest = hash_token(token)
        now = self.clock()
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT expires_at, last_seen_at FROM sessions WHERE token_hash = ?", (digest,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                await conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                await conn.commit()
                return None
            if now - datetime.fromisoformat(row["last_seen_at"]) < TOUCH_EVERY:
                return SessionCheck(refreshed=False)
            await conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (now.isoformat(), (now + SESSION_TTL).isoformat(), digest),
            )
            await conn.commit()
            return SessionCheck(refreshed=True)
        finally:
            await conn.close()

    async def revoke(self, token: str) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
            await conn.commit()
        finally:
            await conn.close()


class LoginLimiter:
    """At most `MAX_FAILURES` failed logins per key in a sliding window; in memory."""

    def __init__(self, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[datetime]:
        failures = self._failures[key]
        cutoff = self._clock() - FAILURE_WINDOW
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            del self._failures[key]  # keeps the map from growing with every address seen
        return failures

    def retry_after(self, key: str) -> int | None:
        """Seconds until another attempt is allowed, or None when one is."""
        failures = self._prune(key)
        if len(failures) < MAX_FAILURES:
            return None
        wait = failures[0] + FAILURE_WINDOW - self._clock()
        return max(1, math.ceil(wait.total_seconds()))

    def fail(self, key: str) -> None:
        self._prune(key)
        self._failures[key].append(self._clock())
