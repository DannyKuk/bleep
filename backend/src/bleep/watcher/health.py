"""How healthy a container, a service and a host are, judged from what the watcher last saw.

Shared by the dashboard's views and by the watcher, which judges a service again when its quiet
ends.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from bleep.hosts.discovery import expects_exit
from bleep.schemas.api import Health, UrlStatus
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.services import HostState
from bleep.watcher.http_checks import INTERVAL_S, UrlCheck

_RANK: dict[Health, int] = {
    "unknown": 0,
    "asleep": 0,
    "ok": 1,
    "stopped": 1,
    "done": 1,
    "degraded": 2,
    "down": 3,
}

# Exit codes of a process that was asked to stop (0, or killed by SIGTERM). 137 is left out: it
# is also what the OOM killer leaves, so it counts as a crash.
CLEAN_EXITS = (0, 143)


def _worst(healths: Iterable[Health]) -> Health:
    return max(healths, key=_RANK.__getitem__, default="unknown")


def is_finished(container: ContainerSummary) -> bool:
    """A one-shot that ran to the end: exited 0 and no restart policy to bring it back."""
    return (
        container.state == "exited"
        and container.exit_code == 0
        and container.restart_policy in ("", "no")
    )


def is_stopped(container: ContainerSummary) -> bool:
    """Exited cleanly, so a human (or a job's end) did it, not a crash."""
    return container.state == "exited" and container.exit_code in CLEAN_EXITS


def container_health(container: ContainerSummary, expected_exit: bool = False) -> Health:
    """One container's health: running and healthy (or no healthcheck) is ok; starting or
    restarting is degraded; a job that exited 0 (a one-shot, or `expected_exit`: its service
    names it in `exits`) is done; another clean exit is stopped; unhealthy, crashed, dead,
    created and paused are down."""
    exited_clean = container.state == "exited" and container.exit_code == 0
    if exited_clean and (expected_exit or is_finished(container)):
        return "done"
    if container.state == "running":
        return {"healthy": "ok", "none": "ok", "starting": "degraded", "unhealthy": "down"}[
            container.health
        ]
    if container.state == "restarting":
        return "degraded"
    if is_stopped(container):
        return "stopped"
    if container.state in ("exited", "dead", "created", "paused"):
        return "down"
    return "unknown"  # a state Bleep has no rule for, such as removing


def service_health(
    containers: Iterable[ContainerSummary],
    url_statuses: Iterable[str],
    exits: list[str] | None = None,
) -> Health:
    """The worst of its containers and its URLs (a failing URL is down); unknown with no data.

    Finished jobs (`done`: exit 0 as a one-shot, or named in the service's `exits`) are left out
    while anything else is there, and a service of finished jobs only is `done`. When everything
    has exited cleanly the service is `stopped`, whatever its URLs say.
    """
    members = list(containers)
    judged = [container_health(c, expects_exit(exits or [], c.name, c.labels)) for c in members]
    if members and all(h == "done" for h in judged):
        return "done"
    healths: list[Health] = [h for h in judged if h != "done"]
    if healths and all(h == "stopped" for h in healths):
        return "stopped"
    # One container stopped on purpose among running ones leaves the service partly working.
    healths = ["degraded" if h == "stopped" else h for h in healths]
    from_urls: list[Health] = [
        {"ok": "ok", "failing": "down"}.get(status, "unknown") for status in url_statuses
    ]
    return _worst([*healths, *from_urls])


def is_asleep(host: HostState) -> bool:
    """A host that may sleep (a laptop) and does not answer: it is asleep, and so is everything
    on it."""
    return not host.reachable and host.config.may_sleep


def host_health(
    reachable: bool,
    last_seen: datetime | None,
    services: Iterable[Health],
    may_sleep: bool = False,
) -> Health:
    """Asleep when a host that may sleep is unreachable; else unknown when unreachable with no
    snapshot, down when unreachable, else the worst service (ok when it has none; a stopped
    service counts as ok)."""
    if not reachable:
        if may_sleep:
            return "asleep"
        return "unknown" if last_seen is None else "down"
    healths: list[Health] = ["ok" if h in ("stopped", "done") else h for h in services]
    return _worst(healths) if healths else "ok"


# A stored result older than this (three check intervals: the host's watcher has stopped, or the
# host is down) no longer says how the URL is now, so it is shown as unknown.
STALE_AFTER = timedelta(seconds=3 * INTERVAL_S)


def url_status(url: str, stored: tuple[UrlCheck, datetime] | None, now: datetime) -> UrlStatus:
    """A configured URL's last stored check; unknown with none, or with one gone stale (the time,
    status code and latency of that last check stay on it)."""
    if stored is None:
        return UrlStatus(
            url=url, status="unknown", last_checked=None, status_code=None, latency_ms=None
        )
    check, at = stored
    fresh = now - at <= STALE_AFTER
    return UrlStatus(
        url=url,
        status=("ok" if check.ok else "failing") if fresh else "unknown",
        last_checked=at,
        status_code=check.status,
        latency_ms=check.latency_ms,
    )
