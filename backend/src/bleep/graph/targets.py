"""Reading a step's target on its host now, for Approval's snapshot and Execute's check.

A Coolify target is the application its containers' labels name, read from a fresh listing
rather than the watcher's last one, so an application recreated a moment ago already shows its
new uuid (#139); any other target is the container it names. Reasons are fixed words: engine
messages are host text and do not belong in Bleep's own, and a plain engine error (the daemon
answered with a refusal) gets a reason of its own instead of ending the run (#618). See
docs/design/graph.md.
"""

from bleep.guard.plan_check import coolify_members
from bleep.hosts.coolify_names import COOLIFY_UUID
from bleep.hosts.engine import ContainerNotFound, EngineError, EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.docker import ContainerSummary
from bleep.schemas.shown import Shown

GONE = Shown.of("target/gone")
UNREACHABLE = Shown.of("target/unreadable_unreachable")
REFUSED = Shown.of("target/unreadable_refused")


async def read_container(
    registry: Registry, host_id: str, ref: str
) -> tuple[ContainerSummary, None] | tuple[None, Shown]:
    """The container `ref` names on the host now, or None and a fixed reason."""
    try:
        return (await registry.engine(host_id).inspect(ref)).summary, None
    except ContainerNotFound:
        return None, GONE
    except EngineUnavailable:
        return None, UNREACHABLE
    except EngineError:
        return None, REFUSED


async def read_app_uuid(
    registry: Registry, host_id: str, target: str
) -> tuple[str, None] | tuple[None, Shown]:
    """The uuid of the one Coolify application `target` names on the host now, or None and why
    not."""
    try:
        containers = await registry.engine(host_id).containers()
    except EngineUnavailable:
        return None, UNREACHABLE
    except EngineError:
        return None, Shown.of("target/unreadable_unlisted")
    members = coolify_members(containers, target)
    uuids = {c.labels[COOLIFY_UUID] for c in members if c.labels.get(COOLIFY_UUID)}
    if len(uuids) != 1:
        return None, Shown.of("target/no_single_app")
    return uuids.pop(), None
