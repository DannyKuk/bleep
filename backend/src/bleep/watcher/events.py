"""Turn Docker events and container snapshots into signals. Pure: no clock, no I/O.

The poller (`poller.py`) fetches a window of events and a fresh container listing each tick and
hands both here. Events are the precise record; the snapshot diff catches what they missed.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from bleep.hosts.discovery import SELF_LABEL
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Signal, Subject

# Exit codes of a container that was asked to stop: 0, or SIGTERM (137, SIGKILL, may be an OOM).
CLEAN_EXITS = (0, 143)

EventKey = tuple[str | None, str, datetime]
SubjectOf = Callable[[str, str | None], Subject]  # (container name, container id)


def event_key(event: DockerEvent) -> EventKey:
    """The identity of an event, so a window that overlaps the last one does not repeat it."""
    return (event.container_id, event.action, event.time)


@dataclass(frozen=True)
class Translation:
    signals: list[Signal]
    died_ids: set[str]  # containers a death signal was emitted for in this batch


def _is_self(event: DockerEvent, known: ContainerSummary | None) -> bool:
    if event.attributes.get(SELF_LABEL) == "true":
        return True
    return known is not None and known.labels.get(SELF_LABEL) == "true"


def _exit_code(event: DockerEvent) -> int | None:
    raw = event.attributes.get("exitCode")
    return int(raw) if raw is not None and raw.lstrip("-").isdigit() else None


def translate(
    events: Iterable[DockerEvent],
    known: dict[str, ContainerSummary],
    subject_of: SubjectOf,
    covered: dict[str, datetime],
) -> Translation:
    """Signals for one batch of events, oldest first.

    - `die` is `container_died` with its exit code and the container's restart policy.
    - `oom` is `oom`; `health_status: unhealthy` is `unhealthy`.
    - `restart` (the restart policy brought a container back) is a death too, unless the batch
      already holds its `die`. The kind `restart_loop` stays the detector's to produce.
    - A container with a `stop` or `destroy` event in the batch was stopped or removed on purpose:
      its `die` and `restart` make no signal.
    - `bleep.self` containers make nothing.
    - `covered` maps a container id to the time the snapshot diff already reported its death; an
      event at or before that time is that same death and is skipped.
    """
    batch = sorted(events, key=lambda e: e.time)
    deliberate = {
        e.container_id for e in batch if e.action in ("stop", "destroy") and e.container_id
    }
    died = {e.container_id for e in batch if e.action == "die" and e.container_id}
    signals: list[Signal] = []
    died_ids: set[str] = set()
    for e in batch:
        cid = e.container_id
        if cid is None or e.type != "container":
            continue
        summary = known.get(cid)
        if _is_self(e, summary):
            continue
        if e.action in ("die", "restart", "oom") and cid in covered and e.time <= covered[cid]:
            continue
        name = e.container_name or (summary.name if summary else cid[:12])
        subject = subject_of(name, cid)
        policy = summary.restart_policy if summary else ""
        match e.action:
            case "die" if cid not in deliberate:
                kind, detail = (
                    "container_died",
                    {"exit_code": _exit_code(e), "restart_policy": policy},
                )
            case "restart" if cid not in deliberate and cid not in died:
                kind, detail = "container_died", {"exit_code": None, "restart_policy": policy}
            case "oom":
                kind, detail = "oom", {}
            case "health_status: unhealthy":
                kind, detail = "unhealthy", {}
            case _:
                continue
        if kind == "container_died":
            died_ids.add(cid)
        signals.append(
            Signal(subject=subject, kind=kind, at=e.time, detail=detail, container=name)  # type: ignore[arg-type]
        )
    return Translation(signals, died_ids)


def diff(
    previous: list[ContainerSummary],
    current: list[ContainerSummary],
    now: datetime,
    subject_of: SubjectOf,
    handled: set[str],
    deliberate: set[str],
) -> list[Signal]:
    """Deaths the events did not report, from the change between two snapshots.

    A container that was running and is now gone, exited with a crash exit code, or has a higher
    restart count is reported once, on the tick where the change first shows. `handled` holds the
    ids the events already reported this tick; `deliberate` those stopped or removed on purpose.
    A clean exit is not a crash, so it makes no signal: the service shows `stopped` instead.
    """
    now_by_id = {c.id: c for c in current}
    signals: list[Signal] = []
    for before in previous:
        if before.labels.get(SELF_LABEL) == "true" or before.state != "running":
            continue
        if before.id in handled or before.id in deliberate:
            continue
        after = now_by_id.get(before.id)
        if after is None:
            exit_code = None  # removed while we were not looking: the exit code is lost
        elif (after.state == "running" and after.restart_count > before.restart_count) or (
            after.state in ("exited", "dead") and after.exit_code not in CLEAN_EXITS
        ):
            exit_code = after.exit_code
        else:
            continue
        signals.append(
            Signal(
                subject=subject_of(before.name, before.id),
                kind="container_died",
                at=now,
                detail={"exit_code": exit_code, "restart_policy": before.restart_policy},
                container=before.name,
            )
        )
    return signals
