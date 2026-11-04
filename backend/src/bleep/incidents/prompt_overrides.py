"""The dev panel's prompt overrides, kept in SQLite and put in force in the prompt loader.

An override must pass exactly the loader's checks (`prompts.check_override`) and holds no secret,
checked when it is saved and again when it is loaded. Reset deletes it, and the loader falls back
to the file. See docs/design/dev-panel.md.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from bleep import prompts
from bleep.guard.secret_fields import find_secrets
from bleep.incidents.repository import utcnow
from bleep.store.db import Database

log = logging.getLogger(__name__)


class UnknownPrompt(LookupError):
    """No prompt of that name can be overridden."""

    def __str__(self) -> str:
        return "no prompt of that name can be overridden"


class PromptRefused(ValueError):
    """The override is not saved; the text says why and never quotes the override."""


@dataclass(frozen=True)
class PromptOverride:
    text: str
    updated_at: datetime


class PromptOverrideStore:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self._db = db
        self._clock = clock
        self._overrides: dict[str, PromptOverride] = {}
        self._lock = asyncio.Lock()

    def overrides(self) -> dict[str, PromptOverride]:
        return dict(self._overrides)

    async def load(self) -> None:
        """Read the stored overrides and put them in force. One that no longer fits (its file
        is gone or its placeholders changed with an upgrade, or it holds a secret) is logged and
        left out: that prompt is read from its file."""
        conn = await self._db.connect()
        try:
            async with conn.execute("SELECT name, text, updated_at FROM prompt_overrides") as cur:
                rows = await cur.fetchall()
        finally:
            await conn.close()
        async with self._lock:
            self._overrides = {}
            for row in rows:
                try:
                    _check(row["name"], row["text"])
                except (UnknownPrompt, PromptRefused) as problem:
                    log.warning("prompt override %s left out: %s", row["name"], problem)
                    continue
                updated_at = datetime.fromisoformat(row["updated_at"])
                self._overrides[row["name"]] = PromptOverride(row["text"], updated_at)
            self._apply()

    async def set(self, name: str, text: str) -> PromptOverride:
        """Store `text` as the prompt `name` and put it in force. Raises `UnknownPrompt` or
        `PromptRefused` with nothing changed."""
        _check(name, text)
        override = PromptOverride(text, self._clock())
        async with self._lock:
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
        return override

    async def reset(self, name: str) -> None:
        """Delete the override, so `name` is read from its file again."""
        if name not in prompts.overridable_names():
            raise UnknownPrompt(name)
        async with self._lock:
            conn = await self._db.connect()
            try:
                await conn.execute("DELETE FROM prompt_overrides WHERE name = ?", (name,))
                await conn.commit()
            finally:
                await conn.close()
            self._overrides.pop(name, None)
            self._apply()

    def close(self) -> None:
        """Put every file back in the loader; the stored overrides stay for the next start."""
        prompts.use_overrides({})

    def _apply(self) -> None:
        prompts.use_overrides({name: o.text for name, o in self._overrides.items()})


def _check(name: str, text: str) -> None:
    if name not in prompts.overridable_names():
        raise UnknownPrompt(name)
    if find_secrets(text):
        raise PromptRefused(
            "a prompt never holds a secret, and this text looks like it does. "
            "Secrets go in the environment"
        )
    try:
        prompts.check_override(name, text)
    except prompts.PromptError as error:
        raise PromptRefused(str(error)) from None
