"""A host's disk, memory and load as readings, and when one is past its threshold.

Pure functions over `HostBasics` (`hosts/shell.host_basics`): the watcher's host probe turns a
reading into signals and the detector opens an incident on them; Verify reads the host again and
judges the same readings by the same rule.
"""

from collections.abc import Mapping
from typing import Any, Literal

from bleep.schemas.docker import HostBasics
from bleep.schemas.incidents import SignalKind
from bleep.schemas.settings import Thresholds


def readings(basics: HostBasics, *, load_minutes: Literal[1, 15] = 15) -> dict[SignalKind, dict]:
    """The disk, memory and load details of one reading, keyed by signal kind, as
    `past_threshold` reads them; a kind the host did not report is left out. Disk is the fullest
    mount, load is per CPU over the last `load_minutes`."""
    found: dict[SignalKind, dict] = {}
    if basics.disks:
        worst = max(basics.disks, key=lambda d: d.used_percent)
        found["disk"] = {"percent": worst.used_percent, "mount": worst.mount}
    if basics.mem_total and basics.mem_available is not None:
        found["memory"] = {"available_percent": basics.mem_available / basics.mem_total * 100}
    load = basics.load1 if load_minutes == 1 else basics.load15
    if load is not None and basics.cpus:
        found["load"] = {"load_per_cpu": load / basics.cpus}
    return found


def past_threshold(kind: str, detail: Mapping[str, Any], t: Thresholds) -> bool:
    """Whether one disk, memory or load reading is past its threshold. One reading only: the
    detector opens on memory and load after several in a row."""
    match kind:
        case "disk":
            return _value(detail, "percent") >= t.disk_percent
        case "memory":
            return _value(detail, "available_percent", 100) < t.memory_available_percent_min
        case "load":
            return _value(detail, "load_per_cpu") > t.load_per_cpu
    raise ValueError(f"not a host reading: {kind}")


def _value(detail: Mapping[str, Any], key: str, default: float = 0) -> float:
    value = detail.get(key, default)
    return value if isinstance(value, int | float) else default
