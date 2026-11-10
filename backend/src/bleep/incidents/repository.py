"""Incidents and their signals in SQLite. At most one incident is open per subject."""

import json
import logging
import secrets
import sqlite3
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from bleep.guard.titles import build_title, split_service_id, title_name
from bleep.schemas.incidents import (
    LOOK_REQUESTED,
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
        short_name=row["short_name"],
    )


# Opening kinds that are one cause told apart only by how it showed; the others keep their own key,
# so a restart loop is never counted as one more single death.
_DEATHS = ("container_died", "oom", "down_past_grace")


# The detector sends the deaths before what they add up to (`[*deaths, restart_loop]`, a pending
# death before `unhealthy`), so the incident's kind is its most telling signal, not its first.
_PRECEDENCE = ("restart_loop", "unhealthy")


def primary_signal(signals: list[Signal]) -> Signal | None:
    """The incident's most telling signal: a restart loop, then `unhealthy`, then any other kind,
    then a death."""
    for kind in _PRECEDENCE:
        if found := next((s for s in signals if s.kind == kind), None):
            return found
    others = [s for s in signals if s.kind not in _DEATHS]
    return others[0] if others else (signals[0] if signals else None)


def fold_key(subject: Subject, signals: list[Signal]) -> str:
    """What makes an incident a repeat of another: its subject, its primary kind (a restart
    loop, then `unhealthy`, then any other kind, then a death) and that signal's target (the
    container, or the URL)."""
    primary = primary_signal(signals)
    kind = "death" if primary is None or primary.kind in _DEATHS else primary.kind
    target = ""
    if primary is not None:
        url = primary.detail.get("url")
        target = str(url) if primary.kind == "url_failed" else primary.container or ""
    return _fold_key(subject, kind, target)


def is_look(signals: list[Signal]) -> bool:
    """An incident a person asked for (#403): its signals are one look on request."""
    primary = primary_signal(signals)
    return primary is not None and primary.kind == LOOK_REQUESTED


def container_fold_keys(subject: Subject, container: str) -> list[str]:
    """The fold keys of a container's deaths, its restart loops and its turns unhealthy."""
    return [_fold_key(subject, kind, container) for kind in ("death", "restart_loop", "unhealthy")]


def _fold_key(subject: Subject, kind: str, target: str) -> str:
    return f"{subject.key}|{kind}|{target}"


@dataclass(frozen=True)
class FoldGroup:
    """A group of repeats: its first incident, how many incidents it holds, since when."""

    root: str
    count: int
    since: datetime


@dataclass(frozen=True)
class Repeats:
    """Finished incidents a new one would repeat: how many, and the latest of them."""

    count: int
    last_closed: datetime
    last_id: str


# Tokens and cost are the usage ledger's sums, so an incident and the ledger never disagree.
_COLUMNS = (
    "id, kind, host_id, service_id, status, title, opened_at, closed_at, trace_url,"
    " prompt_overrides, fold_of, short_name,"
    " (SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM usage"
    "  WHERE usage.incident_id = incidents.id) AS tokens,"
    " (SELECT SUM(usd) FROM usage WHERE usage.incident_id = incidents.id) AS cost_usd"
)

_OPEN = f"status NOT IN ({', '.join(repr(s) for s in TERMINAL_STATUSES)})"
# Not a look on request (#403) that saw nothing but the look: its fold key is its subject's key,
# its kind and no target, and no watcher signal attached to it while it was open.
_NOT_A_BARE_LOOK = (
    f"NOT (COALESCE(fold_key, '') = subject_key || '|{LOOK_REQUESTED}|'"
    " AND NOT EXISTS (SELECT 1 FROM incident_signals s WHERE s.incident_id = incidents.id"
    f" AND json_extract(s.signal, '$.kind') != '{LOOK_REQUESTED}'))"
)


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
        self,
        subject: Subject,
        signals: list[Signal],
        repeat_of: str | None = None,
        name: str | None = None,
        *,
        attach: bool = True,
    ) -> tuple[Incident, bool]:
        """Attach `signals` to the subject's open incident, or open one; True when opened.
        With `attach` False an open incident takes nothing: it is returned as it is, and only a
        new one gets `signals` (a look on request, #403).

        A new incident gets its short name (`short_name`) from `name`, the service's name as the
        dashboard shows it, when given.

        A new incident folds into the group of the latest finished one with its fold key, else
        into the group of `repeat_of` (a finished incident of another kind that the same failure
        outlived), either only within the fold window.

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
                        target = await self._insert(conn, subject, signals, repeat_of, name)
                    for signal in signals if created or attach else []:
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
        self,
        conn: aiosqlite.Connection,
        subject: Subject,
        signals: list[Signal],
        repeat_of: str | None,
        name: str | None,
    ) -> str:
        now = self.clock()
        incident_id = new_incident_id(now)
        key = fold_key(subject, signals)
        look = is_look(signals)
        # A look names its service as the person knows it, not by an id (#421).
        title = build_title(subject, signals, name if look else None)
        # A look is asked for, never a repeat: its opener always reaches the chat (#403).
        group = None if look else await self._group_to_join(conn, key, now, repeat_of)
        if group is not None:
            # The title carries the count, so every message about the repeat says it.
            title += f" (seen {group.count + 1} times since {group.since:%d %b %H:%M} UTC)"
        await conn.execute(
            "INSERT INTO incidents (id, kind, host_id, service_id, subject_key, status, title,"
            " opened_at, fold_key, fold_of, short_name)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
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
                await self._short_name(conn, subject, name),
            ),
        )
        return incident_id

    async def _short_name(
        self, conn: aiosqlite.Connection, subject: Subject, name: str | None
    ) -> str:
        """`<name> #<n>`, n counting every incident of the subject: the hosts file's id for a
        host, a service's name through `title_name` (#311), since a label chose it."""
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM incidents WHERE subject_key = ?", (subject.key,)
        ) as cursor:
            row = await cursor.fetchone()
        if subject.kind == "host":
            shown = subject.host_id
        else:
            shown = title_name(name or split_service_id(subject)[1])
        return f"{shown} #{row['n'] + 1}"

    async def _group_to_join(
        self, conn: aiosqlite.Connection, key: str, now: datetime, repeat_of: str | None
    ) -> FoldGroup | None:
        """The group a new incident with `key` repeats: the latest finished one with that key
        opened within the window, else `repeat_of` when it opened within it; the window slides
        with each repeat."""
        window_h = self.fold_window_h()
        if window_h <= 0:
            return None
        start = (now - timedelta(hours=window_h)).isoformat()
        repeated = [("fold_key", key)] + ([("id", repeat_of)] if repeat_of is not None else [])
        for column, value in repeated:
            async with conn.execute(
                f"SELECT id, fold_of FROM incidents WHERE {column} = ? AND NOT {_OPEN}"
                " AND opened_at >= ? ORDER BY opened_at DESC LIMIT 1",
                (value, start),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                groups = await self._groups(conn, [row["fold_of"] or row["id"]])
                return next(iter(groups.values()))
        return None

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

    async def finished_since(
        self, subject: Subject, since: datetime, keys: Collection[str] | None = None
    ) -> Repeats | None:
        """The subject's finished incidents opened at or after `since`: those with one of the
        fold `keys`, or of any kind when `keys` is None; None when there is none. A look on
        request (#403) that took no watcher signal is not one: what the watcher holds back must
        not depend on whether someone asked. One that took a failure while open is, as any
        incident that took it would be."""
        column, values = ("subject_key", [subject.key])
        if keys is not None:
            column, values = ("fold_key", list(keys))
        marks = ", ".join("?" for _ in values)
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT id, COUNT(*) OVER () AS n,"
                " MAX(COALESCE(closed_at, opened_at)) OVER () AS last FROM incidents"
                f" WHERE {column} IN ({marks}) AND NOT {_OPEN} AND {_NOT_A_BARE_LOOK}"
                " AND opened_at >= ?"
                " ORDER BY opened_at DESC, id DESC LIMIT 1",
                (*values, since.isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        return Repeats(row["n"], datetime.fromisoformat(row["last"]), row["id"])

    async def kinds(self, incidents: Collection[Incident]) -> dict[str, str]:
        """Each incident's kind by its id, as its fold key holds it (`host_unreachable`, `disk`,
        `restart_loop`, `death` for any death, ...); one from before fold keys is left out."""
        by_id = {i.id: i for i in incidents}
        if not by_id:
            return {}
        marks = ", ".join("?" for _ in by_id)
        conn = await self.db.connect()
        try:
            async with conn.execute(
                "SELECT id, fold_key FROM incidents"
                f" WHERE id IN ({marks}) AND fold_key IS NOT NULL",
                list(by_id),
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        kinds = {}
        for row in rows:
            subject_key = by_id[row["id"]].subject.key
            kinds[row["id"]] = row["fold_key"][len(subject_key) + 1 :].split("|", 1)[0]
        return kinds

    async def previous(self, incident: Incident) -> Incident | None:
        """The subject's incident that closed last before `incident` opened, if any; a look on
        request that took no watcher signal (#403) is not one: it saw no problem that could come
        back."""
        conn = await self.db.connect()
        try:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM incidents WHERE subject_key = ? AND id != ?"
                f" AND NOT {_OPEN} AND closed_at IS NOT NULL AND opened_at <= ?"
                f" AND {_NOT_A_BARE_LOOK}"
                " ORDER BY closed_at DESC, id DESC LIMIT 1",
                (incident.subject.key, incident.id, incident.opened_at.isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        return _incident(row) if row is not None else None

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
        host_id: str | None = None,
    ) -> list[Incident]:
        """Newest first, optionally only those with `status`, of the service `service_id`, the
        repeats of `fold_of`, or the host `host_id`'s own (its subject, not its services')."""
        filters = {
            "status": status,
            "service_id": service_id,
            "fold_of": fold_of,
            "kind": None if host_id is None else "host",
            "host_id": host_id,
        }
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

    async def open_target(self, subject: Subject) -> Incident | None:
        """The open incident a signal of `subject` would attach to: its own, or for a service
        its host's open incident when that one has a `host_unreachable` signal."""
        conn = await self.db.connect()
        try:
            target = await self._open_target(conn, subject)
            return await self._get(conn, target) if target is not None else None
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
