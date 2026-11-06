"""Host text as the prompts show it: through the guard, with Bleep's own numbers left as they are.

A host's basics carry text the host wrote (the OS name, mount names, which on macOS are disk and
USB labels), and a service's name comes from a container label. Both go through the guard before
any prompt holds them (#295); the memory and load figures are Bleep's own reading and stay
outside the wrapper.
"""

from bleep.guard.pipeline import guard_name, guard_text
from bleep.schemas.docker import HostBasics
from bleep.schemas.graph import GatherContext
from bleep.schemas.incidents import Subject


def gib(n: int) -> str:
    return f"{n / 2**30:.1f} GiB"


def describe_basics(basics: HostBasics) -> list[str]:
    """The OS and disks wrapped as untrusted, then the memory and load lines."""
    host_text = [f"os: {basics.os or 'unknown'}"]
    if basics.disks:
        disks = ", ".join(f"{d.mount} {d.used_percent:.0f}%" for d in basics.disks)
        host_text.append(f"disks: {disks}")
    lines = [guard_text("host_basics", "\n".join(host_text))[0]]
    if basics.mem_total and basics.mem_available is not None:
        share = basics.mem_available / basics.mem_total
        lines.append(f"memory available: {share:.0%} of {gib(basics.mem_total)}")
    if basics.load15 is not None:
        cpus = f" on {basics.cpus} CPUs" if basics.cpus else ""
        lines.append(f"load: {basics.load1} (1 min), {basics.load15} (15 min){cpus}")
    return lines


def service_name(subject: Subject, context: GatherContext | None) -> str:
    """The service's name, or its id when Gather found none, through `guard_name`."""
    name = (context.service_name if context else None) or subject.service_id or ""
    return guard_name("service_name", name)
