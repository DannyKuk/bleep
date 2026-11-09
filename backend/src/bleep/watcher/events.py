"""Turn Docker events and container snapshots into signals. Pure: no clock, no I/O.

The poller (`poller.py`) fetches a window of events and a fresh container listing each tick and
hands both here. Events are the precise record; the snapshot diff catches what they missed.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from bleep.hosts.discovery import compose_service, is_self
from bleep.schemas.docker import ContainerSummary, DockerEvent
from bleep.schemas.incidents import Signal, Subject

# Exit codes of a container that was asked to stop: 0, or SIGTERM (137, SIGKILL, may be an OOM).
CLEAN_EXITS = (0, 143)

EventKey = tuple[str | None, str, datetime]
# (container name, container id, its labels): an event's container may never have been listed.
SubjectOf = Callable[[str, str | None, dict[str, str]], Subject]
# Whether the service names the container in its `exits` (a job meant to finish).
ExpectsExit = Callable[[Subject, str, dict[str, str]], bool]


def _never(subject: Subject, name: str, labels: dict[str, str]) -> bool:
    return False


def event_key(event: DockerEvent) -> EventKey:
    """The identity of an event, so a window that overlaps the last one does not repeat it."""
    return (event.container_id, event.action, event.time)


@dataclass(frozen=True)
class Translation:
    signals: list[Signal]
    died_ids: set[str]  # containers a death signal was emitted for in this batch


def _is_self(event: DockerEvent, known: ContainerSummary | None) -> bool:
    return is_self(event.attributes) or (known is not None and is_self(known.labels))


def _exit_code(event: DockerEvent) -> int | None:
    raw = event.attributes.get("exitCode")
    return int(raw) if raw is not None and raw.lstrip("-").isdigit() else None


def translate(
    events: Iterable[DockerEvent],
    known: dict[str, ContainerSummary],
    subject_of: SubjectOf,
    covered: dict[str, datetime],
    expects: ExpectsExit = _never,
) -> Translation:
    """Signals for one batch of events, oldest first.

    - `die` is `container_died` with its exit code and the container's restart policy.
    - `oom` is `oom`; `health_status: unhealthy` is `unhealthy`.
    - `restart` (the restart policy brought a container back) is a death too, unless the batch
      already holds its `die`. The kind `restart_loop` stays the detector's to produce.
    - A container with a `stop` or `destroy` event in the batch was stopped or removed on purpose:
      its `die` and `restart` make no signal. For a job its service `expects` to exit, only `stop`
      counts: a `compose run --rm` job is removed after every run, a failed one included.
    - A death of such a job carries `expected_exit` in its detail.
    - `bleep.self` containers make nothing.
    - `covered` maps a container id to the time the snapshot diff already reported its death; an
      event at or before that time is that same death and is skipped.
    """
    batch = sorted(events, key=lambda e: e.time)
    stopped = {e.container_id for e in batch if e.action == "stop" and e.container_id}
    removed = {e.container_id for e in batch if e.action == "destroy" and e.container_id}
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
        labels = summary.labels if summary else e.attributes
        subject = subject_of(name, cid, labels)
        policy = summary.restart_policy if summary else ""
        expected = expects(subject, name, labels)
        deliberate = stopped if expected else stopped | removed
        match e.action:
            case "die" if cid not in deliberate:
                kind, detail = (
                    "container_died",
                    {"exit_code": _exit_code(e), "restart_policy": policy},
                )
                if expected:
                    detail["expected_exit"] = True
                if service := compose_service(labels):
                    detail["compose_service"] = service  # "This is expected" names it
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
            Signal(
                subject=subject,
                kind=kind,  # type: ignore[arg-type]
                at=e.time,
                detail=detail,
                container=name,
                container_id=cid,
            )
        )
    return Translation(signals, died_ids)


def diff(
    previous: list[ContainerSummary],
    current: list[ContainerSummary],
    now: datetime,
    subject_of: SubjectOf,
    handled: set[str],
    deliberate: set[str],
    expects: ExpectsExit = _never,
) -> list[Signal]:
    """Deaths the events did not report, from the change between two snapshots.

    A container that was running and is now gone, exited with a crash exit code, or has a higher
    restart count is reported once, on the tick where the change first shows. `handled` holds the
    ids the events already reported this tick; `deliberate` those stopped or removed on purpose.
    The signal is stamped with the container's own time (its finish or its new start), not the
    tick's, so the detector sees a restart as the recovery it is. A clean exit is not a crash, so
    it makes no signal: the service shows `stopped` instead. A job its service `expects` to exit
    that is gone was removed after its run (`compose run --rm`), which is no death either.
    """
    now_by_id = {c.id: c for c in current}
    signals: list[Signal] = []
    for before in previous:
        if is_self(before.labels) or before.state != "running":
            continue
        if before.id in handled or before.id in deliberate:
            continue
        after = now_by_id.get(before.id)
        subject = subject_of(before.name, before.id, before.labels)
        expected = expects(subject, before.name, before.labels)
        died_at = now  # removed while we were not looking: no better time is known
        if after is None and expected:
            continue
        if after is None:
            exit_code = None  # the exit code is lost too
        elif after.state == "running" and after.restart_count > before.restart_count:
            exit_code = after.exit_code
            died_at = after.started_at or now  # the policy already brought it back
        elif after.state in ("exited", "dead") and after.exit_code not in CLEAN_EXITS:
            exit_code = after.exit_code
            died_at = after.finished_at or now
        else:
            continue
        detail: dict[str, object] = {
            "exit_code": exit_code,
            "restart_policy": before.restart_policy,
        }
        if expected:
            detail["expected_exit"] = True
        if service := compose_service(before.labels):
            detail["compose_service"] = service
        signals.append(
            Signal(
                subject=subject,
                kind="container_died",
                at=min(died_at, now),
                detail=detail,
                container=before.name,
                container_id=before.id,
            )
        )
    return signals
