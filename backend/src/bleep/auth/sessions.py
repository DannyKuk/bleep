"""Admin sessions in SQLite, and the login rate limit."""

import hashlib
import hmac
import math
import secrets
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bleep.store.db import Database

SESSION_TTL = timedelta(days=7)
# However often it is used, a session ends this long after the login that started it (#619).
SESSION_MAX_AGE = timedelta(days=30)
TOUCH_EVERY = timedelta(minutes=1)
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_mac(password: str, secret: str) -> str:
    """What the sessions are tied to: the password keyed with the secret, so the database holds
    nothing the password can be read or guessed back from without the secret (#619)."""
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SessionCheck:
    refreshed: bool  # the expiry slid forward, so the cookie should be renewed too


class SessionStore:
    def __init__(
        self,
        db: Database,
        clock: Callable[[], datetime] = _utcnow,
        password_mac: str | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.password_mac = password_mac  # None with the login off: there are no sessions then

    async def bind_password(self) -> bool:
        """At startup: end every session when the password (or the secret) is not the one the
        sessions were started under, so a new password logs everyone out. True when a changed
        one did."""
        mac = self.password_mac
        if mac is None:
            return False
        conn = await self.db.connect()
        try:
            async with conn.execute("SELECT password_mac FROM session_password") as cursor:
                row = await cursor.fetchone()
            if row is not None and hmac.compare_digest(row["password_mac"], mac):
                return False
            await conn.execute("DELETE FROM sessions")
            await conn.execute(
                "INSERT INTO session_password (id, password_mac) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET password_mac = excluded.password_mac",
                (mac,),
            )
            await conn.commit()
            return row is not None
        finally:
            await conn.close()

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
        once a minute), never past `SESSION_MAX_AGE` from the login."""
        digest = hash_token(token)
        now = self.clock()
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT created_at, expires_at, last_seen_at FROM sessions WHERE token_hash = ?",
                (digest,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            ends_at = datetime.fromisoformat(row["created_at"]) + SESSION_MAX_AGE
            if min(datetime.fromisoformat(row["expires_at"]), ends_at) <= now:
                await conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                await conn.commit()
                return None
            if now - datetime.fromisoformat(row["last_seen_at"]) < TOUCH_EVERY:
                return SessionCheck(refreshed=False)
            await conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (now.isoformat(), min(now + SESSION_TTL, ends_at).isoformat(), digest),
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
