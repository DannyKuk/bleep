"""Host text as the prompts show it: through the guard, with Bleep's own numbers left as they are.

A host's basics carry text the host wrote (the OS name, mount names, which on macOS are disk and
USB labels), and a service's name and id come from container labels. Both go through the guard
before any prompt holds them (#295); the memory and load figures are Bleep's own reading and stay
outside the wrapper.
"""

from bleep.guard.pipeline import NAME_CHARS, guard_name, guard_text, is_plain_name
from bleep.guard.titles import build_title, seen_note, split_service_id
from bleep.schemas.docker import HostBasics
from bleep.schemas.graph import GatherContext, UrlReading
from bleep.schemas.incidents import Signal, Subject


def gib(n: int) -> str:
    return f"{n / 2**30:.1f} GiB"


def _percent(value: float) -> str:
    return f"{value:g}%"  # 90.0 reads 90%, 91.4 stays 91.4%


def describe_basics(basics: HostBasics) -> list[str]:
    """The OS and disks wrapped as untrusted, then each memory, load and CPU figure that was
    read, whether or not the others were."""
    host_text = [f"os: {basics.os or 'unknown'}"]
    if basics.disks:
        disks = ", ".join(f"{d.mount} {_percent(d.used_percent)}" for d in basics.disks)
        host_text.append(f"disks: {disks}")
    lines = [guard_text("host_basics", "\n".join(host_text))[0]]
    total, available = basics.mem_total, basics.mem_available
    if total and available is not None:
        lines.append(f"memory available: {available / total:.0%} of {gib(total)}")
    elif total:
        lines.append(f"memory: {gib(total)} in total, available not read")
    elif available is not None:
        lines.append(f"memory available: {gib(available)}, total not read")
    loads = [
        f"{value} ({span})"
        for value, span in ((basics.load1, "1 min"), (basics.load15, "15 min"))
        if value is not None
    ]
    cpus = f" on {basics.cpus} CPUs" if basics.cpus else ""
    if loads:
        lines.append(f"load: {', '.join(loads)}{cpus}")
    elif cpus:
        lines.append(f"CPUs: {basics.cpus}")
    return lines


def describe_urls(readings: list[UrlReading]) -> str:
    """Gather's URL checks, one a line, the result before the URL so a redaction inside the URL
    cannot swallow it. The lines are Bleep's own, but wrapped as untrusted all the same, as host
    text beside them is: defence in depth."""
    lines = [
        (f"passed, answered {r.status}" if r.ok else f"failed, {r.error or 'no answer'}")
        + f": {r.url}"
        for r in readings
    ]
    return guard_text("url_checks", "\n".join(lines))[0]


def service_id_shown(subject: Subject) -> str:
    """The service id as a prompt may show it. The id is `<host>/<kind>:<key>`; the host and the
    kind are Bleep's own, so only the key, taken from a label, is judged: a plain key leaves the
    id as it is, any other wraps the whole id."""
    service_id = subject.service_id or ""
    prefix, key = split_service_id(subject)
    if prefix and is_plain_name(key):
        return service_id
    return guard_text("service_id", service_id, NAME_CHARS)[0]


def service_name(subject: Subject, context: GatherContext | None) -> str:
    """The service's name through `guard_name`, or its id (`service_id_shown`) when Gather found
    none."""
    name = context.service_name if context else None
    return guard_name("service_name", name) if name else service_id_shown(subject)


def incident_title(subject: Subject, signals: list[Signal], stored: str | None) -> str:
    """The incident's title as a prompt may show it, built the way the repository builds it
    ("container died on a/compose:shop") from Bleep's own words and the shown service id, never
    by searching the stored title for the id. The stored title's repeat note is kept only when
    the stored title is exactly the repository's title (`build_title`) plus the note: an id that
    ends like a note is the label's text, which that title quotes (#311)."""
    what = signals[0].kind.replace("_", " ") if signals else "incident"
    where = service_id_shown(subject) if subject.service_id else subject.host_id
    shown = f"{what} on {where}"
    note = seen_note(stored or "")
    if note and stored == f"{build_title(subject, signals)} {note}":
        shown += f" {note}"
    return shown
