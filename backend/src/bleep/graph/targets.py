"""Reading a Coolify step's target on its host now, for Approval's snapshot and Execute's check.

The target is the application its containers' labels name, read from a fresh listing rather
than the watcher's last one, so an application recreated a moment ago already shows its new uuid
(#139). Reasons are fixed words: engine messages are host text and do not belong in Bleep's own.
See docs/design/graph.md.
"""

from bleep.guard.plan_check import coolify_members
from bleep.hosts.coolify_names import COOLIFY_UUID
from bleep.hosts.engine import EngineError, EngineUnavailable
from bleep.hosts.registry import Registry
from bleep.schemas.shown import Shown

UNREACHABLE = Shown.of("target/unreadable_unreachable")
UNKNOWN_HOST = Shown.of("target/unreadable_unknown")


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
    except KeyError:
        return None, UNKNOWN_HOST
    members = coolify_members(containers, target)
    uuids = {c.labels[COOLIFY_UUID] for c in members if c.labels.get(COOLIFY_UUID)}
    if len(uuids) != 1:
        return None, Shown.of("target/no_single_app")
    return uuids.pop(), None
