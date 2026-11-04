"""The dev panel's prompt overrides, kept in SQLite and put in force in the prompt loader.

An override must pass exactly the loader's checks (`prompts.check_override`) and holds no secret,
checked when it is saved and again when it is loaded. Reset deletes it, and the loader falls back
to the file. A save or reset names the prompt's `version` it was built from and is refused when
the prompt has changed since; each one that changes something is recorded in the guard log by
name, never with its text. See docs/design/dev-panel.md.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from bleep import prompts
from bleep.guard.log import GuardLogger
from bleep.guard.sanitize import MAX_LINE
from bleep.guard.secret_fields import find_secrets
from bleep.incidents.repository import utcnow
from bleep.store.db import Database

log = logging.getLogger(__name__)

GUARD_INCIDENT = "dev-panel"  # guard_log rows for overrides belong to no incident


class UnknownPrompt(LookupError):
    """No prompt of that name can be overridden."""

    def __str__(self) -> str:
        return "no prompt of that name can be overridden"


class PromptRefused(ValueError):
    """The override is not saved; the text says why and never quotes the override."""


class PromptStale(Exception):
    """The change was built from a version of the prompt that has changed since; nothing is
    applied. `current` is the version now."""

    def __init__(self, current: str) -> None:
        super().__init__("the prompt changed since this version was read")
        self.current = current


@dataclass(frozen=True)
class PromptOverride:
    text: str
    updated_at: datetime


class PromptOverrideStore:
    def __init__(
        self, db: Database, guard_log: GuardLogger, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self._db = db
        self._guard_log = guard_log
        self._clock = clock
        self._overrides: dict[str, PromptOverride] = {}
        self._lock = asyncio.Lock()

    def overrides(self) -> dict[str, PromptOverride]:
        return dict(self._overrides)

    def version(self, name: str) -> str:
        """A hash of the override in force for `name` (or of none), from its content, so it holds
        across a restart."""
        override = self._overrides.get(name)
        return prompts.text_version(override.text if override is not None else None)

    async def load(self) -> None:
        """Read the stored overrides and put them in force. One of a prompt that can no longer
        be overridden (its file is gone, or it was taken out of `prompts.OVERRIDABLE`) is deleted
        and logged once. One that no longer fits (its placeholders changed with an upgrade, or it
        holds a secret) is logged and left out, and stays stored. Either way that prompt is read
        from its file."""
        overridable = set(prompts.overridable_names())
        conn = await self._db.connect()
        try:
            async with conn.execute("SELECT name, text, updated_at FROM prompt_overrides") as cur:
                rows = await cur.fetchall()
            unknown = [row["name"] for row in rows if row["name"] not in overridable]
            if unknown:
                marks = ", ".join("?" * len(unknown))
                await conn.execute(f"DELETE FROM prompt_overrides WHERE name IN ({marks})", unknown)
                await conn.commit()
        finally:
            await conn.close()
        for name in unknown:
            log.warning("prompt override %s deleted: %s", name, UnknownPrompt(name))
        async with self._lock:
            self._overrides = {}
            for row in rows:
                if row["name"] in unknown:
                    continue
                try:
                    _check(row["name"], row["text"])
                except PromptRefused as problem:
                    log.warning("prompt override %s left out: %s", row["name"], problem)
                    continue
                updated_at = datetime.fromisoformat(row["updated_at"])
                self._overrides[row["name"]] = PromptOverride(row["text"], updated_at)
            self._apply()

    async def set(self, name: str, text: str, *, version: str) -> PromptOverride:
        """Store `text` as the prompt `name` and put it in force; `text` was written from the
        prompt at `version`. Raises `UnknownPrompt`, `PromptRefused` or `PromptStale` with nothing
        changed."""
        _check(name, text)
        override = PromptOverride(text, self._clock())
        async with self._lock:
            self._refuse_stale(name, version)
            conn = await self._db.connect()
            try:
                await conn.execute(
                    "INSERT INTO prompt_overrides (name, text, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (name) DO UPDATE SET text = excluded.text, "
                    "updated_at = excluded.updated_at",
                    (name, text, override.updated_at.isoformat()),
                )
                await conn.commit()
            finally:
                await conn.close()
            self._overrides[name] = override
            self._apply()
        self._guard_log.record(GUARD_INCIDENT, "prompt_override", f"override of {name} saved")
        return override

    async def reset(self, name: str, *, version: str) -> None:
        """Delete the override, so `name` is read from its file again; the reset was asked from
        the prompt at `version`. A stored override that `load` left out is deleted too, so a reset
        clears one that no longer fits. Raises `UnknownPrompt` or `PromptStale` with nothing
        changed."""
        if name not in prompts.overridable_names():
            raise UnknownPrompt(name)
        async with self._lock:
            self._refuse_stale(name, version)
            conn = await self._db.connect()
            try:
                cursor = await conn.execute("DELETE FROM prompt_overrides WHERE name = ?", (name,))
                deleted = cursor.rowcount > 0
                await conn.commit()
            finally:
                await conn.close()
            if not deleted:
                return
            if self._overrides.pop(name, None) is None:
                log.info("prompt override %s, left out at start, deleted by a reset", name)
            self._apply()
        self._guard_log.record(GUARD_INCIDENT, "prompt_override", f"override of {name} reset")

    def close(self) -> None:
        """Put every file back in the loader; the stored overrides stay for the next start."""
        prompts.use_overrides({})

    def _refuse_stale(self, name: str, version: str) -> None:
        if version != self.version(name):
            raise PromptStale(self.version(name))

    def _apply(self) -> None:
        prompts.use_overrides({name: o.text for name, o in self._overrides.items()})


def _check(name: str, text: str) -> None:
    if name not in prompts.overridable_names():
        raise UnknownPrompt(name)
    check_lines(text)
    if find_secrets(text):
        raise PromptRefused(
            "a prompt never holds a secret, and this text looks like it does. "
            "Secrets go in the environment"
        )
    try:
        prompts.check_override(name, text)
    except prompts.PromptError as error:
        raise PromptRefused(str(error)) from None


def check_lines(text: str) -> None:
    """Raise `PromptRefused` for what redaction would reshape, so the secret check, which reads
    any change as a secret, is never the reason given: redaction turns other line breaks into
    newlines and cuts a line over 4,000 characters."""
    if any(mark in text for mark in ("\r", "\u2028", "\u2029")):
        raise PromptRefused(
            "line breaks must be plain newlines; CR, CRLF, U+2028 and U+2029 are not allowed"
        )
    if any(len(line) > MAX_LINE for line in text.split("\n")):
        raise PromptRefused(f"a line is over {MAX_LINE:,} characters; break it up")
