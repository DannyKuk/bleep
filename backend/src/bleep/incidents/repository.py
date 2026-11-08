"""Incidents and their signals in SQLite. At most one incident is open per subject."""

import json
import logging
import re
import secrets
import sqlite3
import unicodedata
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from bleep.guard.pipeline import is_plain_name
from bleep.guard.redact import redact_text
from bleep.guard.sanitize import strip_control
from bleep.incidents.quote_lookalikes import QUOTE_LOOKALIKES
from bleep.schemas.incidents import (
    TERMINAL_STATUSES,
    Incident,
    IncidentStatus,
    RecoveryEvent,
    Signal,
    Subject,
)
from bleep.store.db import Database

log = logging.getLogger(__name__)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_incident_id(now: datetime) -> str:
    """`inc_` + 10 chars of millisecond time + 8 random chars: sorts by creation, no dependency."""
    millis = int(now.timestamp() * 1000)
    stamp = "".join(_CROCKFORD[(millis >> shift) & 31] for shift in range(45, -5, -5))
    return "inc_" + stamp + "".join(secrets.choice(_CROCKFORD) for _ in range(8))


def _incident(row: aiosqlite.Row) -> Incident:
    return Incident(
        id=row["id"],
        subject=Subject(kind=row["kind"], host_id=row["host_id"], service_id=row["service_id"]),
        status=row["status"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        title=row["title"],
        cost_usd=row["cost_usd"],
        tokens=row["tokens"],
        trace_url=row["trace_url"],
        prompt_overrides=json.loads(row["prompt_overrides"]) if row["prompt_overrides"] else None,
        fold_of=row["fold_of"],
    )


# Opening kinds that are one cause told apart only by how it showed; the others keep their own key,
# so a restart loop is never counted as one more single death.
_DEATHS = ("container_died", "oom", "down_past_grace")


# The detector sends the deaths before what they add up to (`[*deaths, restart_loop]`, a pending
# death before `unhealthy`), so the incident's kind is its most telling signal, not its first.
_PRECEDENCE = ("restart_loop", "unhealthy")
_SEEN = re.compile(r"\(seen \d+ times since [^)]*\)$")


def _primary(signals: list[Signal]) -> Signal | None:
    for kind in _PRECEDENCE:
        if found := next((s for s in signals if s.kind == kind), None):
            return found
    others = [s for s in signals if s.kind not in _DEATHS]
    return others[0] if others else (signals[0] if signals else None)


def fold_key(subject: Subject, signals: list[Signal]) -> str:
    """What makes an incident a repeat of another: its subject, its primary kind (a restart
    loop, then `unhealthy`, then any other kind, then a death) and that signal's target (the
    container, or the URL)."""
    primary = _primary(signals)
    kind = "death" if primary is None or primary.kind in _DEATHS else primary.kind
    target = ""
    if primary is not None:
        url = primary.detail.get("url")
        target = str(url) if primary.kind == "url_failed" else primary.container or ""
    return f"{subject.key}|{kind}|{target}"


def seen_note(title: str) -> str:
    """The "(seen N times since …)" a repeat's title ends with; empty for any other title."""
    found = _SEEN.search(title)
    return found.group(0) if found else ""


@dataclass(frozen=True)
class FoldGroup:
    """A group of repeats: its first incident, how many incidents it holds, since when."""

    root: str
    count: int
    since: datetime


# The kinds `hosts.discovery` puts before a service key: Bleep's own words, not the label's.
_KEY_KINDS = ("compose:", "container:", "id:")
TITLE_NAME_CHARS = 40  # a name that is not plain, cut to this in a title, "…" included
_MARKS = ("Mn", "Me", "Cs")  # combining marks (a `"` can be drawn on a letter), lone surrogates
_BLANKS = re.compile(r"\s+")
# A name in a title is shown inside straight double quotes, so nothing in it may read as one:
# each character of `QUOTE_LOOKALIKES` becomes `'`, and a run of `'` one, since two read as `"`.
# `ŉ` is drawn as `'n`, so it is written so before the run is collapsed.
_PLAIN_QUOTES = {ord(c): "'" for c in QUOTE_LOOKALIKES} | {0x0149: "'n"}
_APOSTROPHES = re.compile(r"''+")
_SKIN_TONES = ("\U0001f3fb", "\U0001f3ff")
_REGIONAL = ("\U0001f1e6", "\U0001f1ff")


def split_service_id(subject: Subject) -> tuple[str, str]:
    """The service id split into Bleep's own prefix (`<host>/<kind>:`) and the key a label
    wrote. An id that does not start with its host gives no prefix: all of it is the label's."""
    service_id = subject.service_id or ""
    host = f"{subject.host_id}/"
    if not service_id.startswith(host):
        return "", service_id
    key = service_id[len(host) :]
    kind = next((k for k in _KEY_KINDS if key.startswith(k)), "")
    return host + kind, key[len(kind) :]


def _regional(char: str) -> bool:
    return _REGIONAL[0] <= char <= _REGIONAL[1]


def _joins_previous(text: str, at: int) -> bool:
    """Whether `text[at]` is drawn as one with the character before it: a spacing mark, a skin
    tone, or the second letter of a flag. (Zero-width joiners and variation selectors are gone
    by then, `strip_control` drops them.)"""
    char = text[at]
    if unicodedata.category(char) == "Mc" or _SKIN_TONES[0] <= char <= _SKIN_TONES[1]:
        return True
    if not _regional(char):
        return False
    flags = 0  # the flag letters before it: an odd number leaves it the second of a pair
    while flags < at and _regional(text[at - 1 - flags]):
        flags += 1
    return flags % 2 == 1


def _cut(text: str, limit: int) -> str:
    """`text` cut to `limit` characters with "…", never inside what draws as one character."""
    if len(text) <= limit:
        return text
    end = limit - 1
    while end > 0 and _joins_previous(text, end):
        end -= 1
    return text[:end].rstrip() + "…"


def title_name(name: str) -> str:
    """A name from a label as an incident title shows it: a plain one (`is_plain_name`) as it
    is, any other as host text: in double quotes, on one line, redacted, with nothing of its own
    that reads as a double quote, cut to 40 characters with "…"."""
    if is_plain_name(name):
        return name
    text = unicodedata.normalize("NFC", strip_control(name))
    text = "".join(c for c in text if unicodedata.category(c) not in _MARKS)
    text = _BLANKS.sub(" ", redact_text(text)).strip()
    text = _APOSTROPHES.sub("'", text.translate(_PLAIN_QUOTES))
    # Again: a secret in curly quotes (`“password”: “…”`) is found only once they read `'`.
    return f'"{_cut(redact_text(text), TITLE_NAME_CHARS)}"'


def build_title(subject: Subject, signals: list[Signal]) -> str:
    """The title an incident opens with ("container died on a/compose:shop"): Bleep's words for
    its first signal and its subject, a service's key through `title_name`. The host id is the
    hosts file's or the dashboard's, written by the operator, and stands as it is."""
    what = signals[0].kind.replace("_", " ") if signals else "incident"
    if not subject.service_id:
        return f"{what} on {subject.host_id}"
    prefix, key = split_service_id(subject)
    return f"{what} on {prefix}{title_name(key)}"


# Tokens and cost are the usage ledger's sums, so an incident and the ledger never disagree.
_COLUMNS = (
    "id, kind, host_id, service_id, status, title, opened_at, closed_at, trace_url,"
    " prompt_overrides, fold_of,"
    " (SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM usage"
    "  WHERE usage.incident_id = incidents.id) AS tokens,"
    " (SELECT SUM(usd) FROM usage WHERE usage.incident_id = incidents.id) AS cost_usd"
)

_OPEN = f"status NOT IN ({', '.join(repr(s) for s in TERMINAL_STATUSES)})"


class IncidentRepo:
    """`fold_window_h` is read at every open (the threshold of the same name, 0 turns folding
    off)."""

    def __init__(
        self,
        db: Database,
        clock: Callable[[], datetime] = utcnow,
        fold_window_h: Callable[[], float] = lambda: 24,
    ) -> None:
        self.db = db
        self.clock = clock
        self.fold_window_h = fold_window_h
        self._finished: list[Callable[[str], Awaitable[None]]] = []

    def on_finished(self, listener: Callable[[str], Awaitable[None]]) -> None:
        """Call `listener(incident_id)` after an incident reaches a terminal status, by whatever
        path. A listener that raises is logged and never undoes or blocks the close."""
        self._finished.append(listener)

    async def _tell_finished(self, incident_id: str) -> None:
        for listener in self._finished:
            try:
                await listener(incident_id)
            except Exception:
                log.exception("incident %s: a listener for its end failed", incident_id)

    async def open_or_attach(
        self, subject: Subject, signals: list[Signal]
    ) -> tuple[Incident, bool]:
        """Attach `signals` to the subject's open incident, or open one; True when opened.

        A service signal on a host whose open incident has a `host_unreachable` signal attaches
        to that host incident. The write transaction is taken up front and the unique index is
        the backstop, so two concurrent callers for one subject end with one incident.
        """
        conn = await self.db.connect()
        try:
            for attempt in range(2):
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    target = await self._open_target(conn, subject)
                    created = target is None
                    if target is None:
                        target = await self._insert(conn, subject, signals)
                    for signal in signals:
                        await conn.execute(
                            "INSERT INTO incident_signals (incident_id, signal, at)"
                            " VALUES (?, ?, ?)",
                            (target, signal.model_dump_json(), signal.at.isoformat()),
                        )
                    await conn.commit()
                except sqlite3.IntegrityError:
                    await conn.rollback()
                    if attempt:
                        raise
                    continue  # lost a race on the unique index: re-select and attach
                except BaseException:
                    await conn.rollback()
                    raise
                incident = await self._get(conn, target)
                assert incident is not None
                return incident, created
            raise AssertionError("unreachable")
        finally:
            await conn.close()

    async def _open_target(self, conn: aiosqlite.Connection, subject: Subject) -> str | None:
        async with conn.execute(
            f"SELECT id FROM incidents WHERE subject_key = ? AND {_OPEN}", (subject.key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            return row["id"]
        if subject.kind != "service":
            return None
        host_key = Subject(kind="host", host_id=subject.host_id).key
        async with conn.execute(
            f"SELECT i.id FROM incidents i WHERE i.subject_key = ? AND i.{_OPEN}"
            " AND EXISTS (SELECT 1 FROM incident_signals s WHERE s.incident_id = i.id"
            " AND json_extract(s.signal, '$.kind') = 'host_unreachable')",
            (host_key,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["id"] if row is not None else None

    async def _insert(
        self, conn: aiosqlite.Connection, subject: Subject, signals: list[Signal]
    ) -> str:
        now = self.clock()
        incident_id = new_incident_id(now)
        key = fold_key(subject, signals)
        title = build_title(subject, signals)
        group = await self._group_to_join(conn, key, now)
        if group is not None:
            # The title carries the count, so every message about the repeat says it.
            title += f" (seen {group.count + 1} times since {group.since:%d %b %H:%M} UTC)"
        await conn.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at, fold_key, fold_of) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (
                incident_id,
                subject.kind,
                subject.host_id,
                subject.service_id,
                subject.key,
                title,
                now.isoformat(),
                key,
                group.root if group is not None else None,
            ),
        )
        return incident_id

    async def _group_to_join(
        self, conn: aiosqlite.Connection, key: str, now: datetime
    ) -> FoldGroup | None:
        """The group a new incident with `key` repeats: the latest finished one with that key
        opened within the window; the window slides with each repeat."""
        window_h = self.fold_window_h()
        if window_h <= 0:
            return None
        async with conn.execute(
            f"SELECT id, fold_of FROM incidents WHERE fold_key = ? AND NOT {_OPEN}"
            " AND opened_at >= ? ORDER BY opened_at DESC LIMIT 1",
            (key, (now - timedelta(hours=window_h)).isoformat()),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        groups = await self._groups(conn, [row["fold_of"] or row["id"]])
        return next(iter(groups.values()))

    async def _groups(
        self, conn: aiosqlite.Connection, roots: Collection[str]
    ) -> dict[str, FoldGroup]:
        if not roots:
            return {}
        marks = ", ".join("?" for _ in roots)
        async with conn.execute(
            f"SELECT r.id, r.opened_at, (SELECT COUNT(*) FROM incidents f WHERE f.fold_of = r.id)"
            f" AS repeats FROM incidents r WHERE r.id IN ({marks})",
            list(roots),
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            row["id"]: FoldGroup(
                row["id"], row["repeats"] + 1, datetime.fromisoformat(row["opened_at"])
            )
            for row in rows
        }

    async def groups(self, incidents: Collection[Incident]) -> dict[str, FoldGroup]:
        """Each incident's group by its id; one that is not in a group (alone) is left out."""
        roots = {i.id: i.fold_of or i.id for i in incidents}
        conn = await self.db.connect()
        try:
            found = await self._groups(conn, set(roots.values()))
        finally:
            await conn.close()
        return {
            incident_id: found[root]
            for incident_id, root in roots.items()
            if root in found and found[root].count > 1
        }

    async def set_status(self, incident_id: str, status: IncidentStatus) -> None:
        """Set the status of an unfinished incident; a finished one keeps its status, so a run
        racing `close_unfinished` can never open a closed incident again."""
        closed_at = self.clock().isoformat() if status in TERMINAL_STATUSES else None
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "UPDATE incidents SET status = ?, closed_at = COALESCE(?, closed_at)"
                f" WHERE id = ? AND {_OPEN}",
                (status, closed_at, incident_id),
            )
            await conn.commit()
            moved = cursor.rowcount == 1
        finally:
            await conn.close()
        if moved and status in TERMINAL_STATUSES:
            await self._tell_finished(incident_id)

    async def close_unfinished(
        self, incident_id: str, spare: tuple[IncidentStatus, ...] = ()
    ) -> IncidentStatus | None:
        """Close the incident if it is unfinished; the status it had, or None when it was
        finished already (and is left as it was). An incident in a `spare` status is left open
        too and gives None: the check and the close are one transaction."""
        conn = await self.db.connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                f"SELECT status FROM incidents WHERE id = ? AND {_OPEN}", (incident_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row["status"] in spare:
                await conn.commit()
                return None
            await conn.execute(
                "UPDATE incidents SET status = 'closed', closed_at = ? WHERE id = ?",
                (self.clock().isoformat(), incident_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        await self._tell_finished(incident_id)
        return row["status"]

    async def set_trace_url(self, incident_id: str, url: str) -> None:
        conn = await self.db.connect()
        try:
            await conn.execute(
                "UPDATE incidents SET trace_url = ? WHERE id = ?", (url, incident_id)
            )
            await conn.commit()
        finally:
            await conn.close()

    async def record_prompt_overrides(self, incident_id: str, versions: dict[str, str]) -> None:
        """Record the prompt overrides in force as the incident's run starts (name to
        `prompts.text_version`). Only the first record counts: a run started again after a crash
        keeps what it started with."""
        conn = await self.db.connect()
        try:
            await conn.execute(
                "UPDATE incidents SET prompt_overrides = ?"
                " WHERE id = ? AND prompt_overrides IS NULL",
                (json.dumps(versions, sort_keys=True), incident_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _get(self, conn: aiosqlite.Connection, incident_id: str) -> Incident | None:
        async with conn.execute(
            f"SELECT {_COLUMNS} FROM incidents WHERE id = ?", (incident_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _incident(row) if row is not None else None

    async def get(self, incident_id: str) -> Incident | None:
        conn = await self.db.connect()
        try:
            return await self._get(conn, incident_id)
        finally:
            await conn.close()

    async def get_many(self, incident_ids: Collection[str]) -> dict[str, Incident]:
        """The incidents among `incident_ids` that exist, by id, in one query."""
        ids = list(dict.fromkeys(incident_ids))
        if not ids:
            return {}
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM incidents WHERE id IN ({', '.join('?' * len(ids))})", ids
            ) as cursor:
                return {row["id"]: _incident(row) for row in await cursor.fetchall()}
        finally:
            await conn.close()

    async def list(
        self,
        status: IncidentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
        service_id: str | None = None,
        fold_of: str | None = None,
    ) -> list[Incident]:
        """Newest first, optionally only those with `status`, of the service `service_id`, or the
        repeats of `fold_of`."""
        filters = {"status": status, "service_id": service_id, "fold_of": fold_of}
        given = {column: value for column, value in filters.items() if value is not None}
        where = " AND ".join(f"{column} = ?" for column in given)
        args: list[object] = list(given.values())
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM incidents {'WHERE ' + where if where else ''}"
                " ORDER BY opened_at DESC, id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ) as cursor:
                return [_incident(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def unfinished(self) -> list[Incident]:
        """Every incident not yet resolved, closed or failed, oldest first."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM incidents WHERE {_OPEN} ORDER BY opened_at, id"
            ) as cursor:
                return [_incident(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def open_on_host(self, host_id: str) -> list[Incident]:
        """Every unfinished incident on the host, its services' included, oldest first."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM incidents WHERE host_id = ? AND {_OPEN}"
                " ORDER BY opened_at, id",
                (host_id,),
            ) as cursor:
                return [_incident(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()

    async def open_incident_id(self, subject: Subject) -> str | None:
        """The id of the subject's open incident, if it has one."""
        conn = await self.db.connect()
        try:
            return await self._open_for(conn, subject)
        finally:
            await conn.close()

    async def _open_for(self, conn: aiosqlite.Connection, subject: Subject) -> str | None:
        async with conn.execute(
            f"SELECT id FROM incidents WHERE subject_key = ? AND {_OPEN}", (subject.key,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["id"] if row is not None else None

    async def signals(self, incident_id: str) -> list[Signal]:
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT signal FROM incident_signals WHERE incident_id = ? ORDER BY id",
                (incident_id,),
            ) as cursor:
                return [
                    Signal.model_validate_json(row["signal"]) for row in await cursor.fetchall()
                ]
        finally:
            await conn.close()

    async def log_recovery(self, event: RecoveryEvent) -> RecoveryEvent:
        conn = await self.db.connect()
        try:
            cursor = await conn.execute(
                "INSERT INTO recovery_events (host_id, service_id, container, at, explanation)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    event.host_id,
                    event.service_id,
                    event.container,
                    event.at.isoformat(),
                    event.explanation,
                ),
            )
            await conn.commit()
            return event.model_copy(update={"id": cursor.lastrowid})
        finally:
            await conn.close()

    async def recoveries(self, limit: int = 100) -> list[RecoveryEvent]:
        """The latest self-recoveries, newest first."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT * FROM recovery_events ORDER BY at DESC, id DESC LIMIT ?", (limit,)
            ) as cursor:
                return [
                    RecoveryEvent(
                        id=row["id"],
                        host_id=row["host_id"],
                        service_id=row["service_id"],
                        container=row["container"],
                        at=datetime.fromisoformat(row["at"]),
                        explanation=row["explanation"],
                    )
                    for row in await cursor.fetchall()
                ]
        finally:
            await conn.close()


__all__ = ["IncidentRepo", "new_incident_id", "utcnow"]
