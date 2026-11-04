"""The allowlisted, read-only shell of a host, and the host basics read through it.

The allowlist is enforced here, never by a prompt: a command is split with `shlex.split` and
must match one argv template exactly. It is then run as an argv list (see `SshSession.run`),
so no shell ever sees the text. See docs/design/hosts.md.
"""

import asyncio
import logging
import re
import shlex
from collections.abc import Callable
from typing import Literal, Protocol

from bleep.hosts.ssh import CommandResult
from bleep.schemas.docker import DiskUsage, EngineInfo, HostBasics

log = logging.getLogger(__name__)

OS = Literal["Linux", "Darwin"]
COMMAND_TIMEOUT_S = 20.0
JOURNAL_MAX_LINES = 500
_UNIT = re.compile(r"[\w@.-]+", re.ASCII)
_LINES = re.compile(r"[0-9]{1,4}", re.ASCII)

# Exact argv templates. `{n}` is a line count from 1 to 500; `{unit}` a systemd unit name.
ALLOWED: dict[str, list[str]] = {
    "uname": ["uname", "-s"],
    "df": ["df", "-P"],
    "free": ["free", "-b"],
    "loadavg": ["cat", "/proc/loadavg"],
    "nproc": ["nproc"],
    "uptime": ["uptime"],
    "top (Linux)": ["top", "-b", "-n", "1"],
    "top (Darwin)": ["top", "-l", "1", "-n", "15"],
    "journalctl": ["journalctl", "-n", "{n}", "--no-pager"],
    "journalctl unit": ["journalctl", "-n", "{n}", "--no-pager", "-u", "{unit}"],
    "vm_stat": ["vm_stat"],
    "sysctl": ["sysctl", "-n", "hw.memsize", "hw.ncpu", "vm.loadavg"],
}
_ON: dict[str, tuple[OS, ...]] = {
    "free": ("Linux",),
    "loadavg": ("Linux",),
    "nproc": ("Linux",),
    "top (Linux)": ("Linux",),
    "journalctl": ("Linux",),
    "journalctl unit": ("Linux",),
    "top (Darwin)": ("Darwin",),
    "vm_stat": ("Darwin",),
    "sysctl": ("Darwin",),
}


class CommandNotAllowed(Exception):
    pass


class HostShell(Protocol):
    async def run(self, argv: list[str], timeout: float) -> CommandResult: ...


def _on(name: str) -> tuple[OS, ...]:
    return _ON.get(name, ("Linux", "Darwin"))


def _shown(template: list[str]) -> str:
    return " ".join(template)


def allowed_text(os: OS | None = None) -> str:
    """The commands `os` may run, one per line, for a refusal message or a tool description."""
    lines = [
        _shown(t).replace("{n}", "<n 1-500>").replace("{unit}", "<unit>")
        for name, t in ALLOWED.items()
        if os is None or os in _on(name)
    ]
    return "\n".join(lines)


def _matches(template: list[str], argv: list[str]) -> bool:
    if len(template) != len(argv):
        return False
    for want, got in zip(template, argv, strict=True):
        if want == "{n}":
            if not _LINES.fullmatch(got) or not 1 <= int(got) <= JOURNAL_MAX_LINES:
                return False
        elif want == "{unit}":
            if not _UNIT.fullmatch(got) or got.startswith("-"):
                return False
        elif want != got:
            return False
    return True


def _check(command: str, os: OS) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    for name, template in ALLOWED.items():
        if argv and _matches(template, argv):
            if os not in _on(name):
                raise CommandNotAllowed(
                    f"{argv[0]} is not available on {os}. Allowed commands on {os}:\n"
                    f"{allowed_text(os)}"
                )
            return argv
    raise CommandNotAllowed(
        f"command not allowed. Only these exact commands may run on {os}:\n{allowed_text(os)}"
    )


async def run_allowed(shell: HostShell, command: str, os: OS) -> str:
    """Run `command` if it is exactly an allowlisted argv, else raise `CommandNotAllowed`."""
    argv = _check(command, os)
    result = await shell.run(argv, COMMAND_TIMEOUT_S)
    if result.exit_code == 0:
        return result.stdout
    return f"[exit {result.exit_code}] {result.stdout}{result.stderr}".rstrip()


# --- basics -------------------------------------------------------------------------------------

_PSEUDO_FS = {"tmpfs", "devtmpfs", "overlay", "shm", "squashfs", "devfs"}
_DF_ROW = re.compile(r"(?P<fs>.+?)\s+\d+\s+\d+\s+\d+\s+(?P<pct>\d+)%\s+(?P<mount>/.*)")


class HostUnreadable(Exception):
    """A basics command failed (non-zero exit) or its output did not parse."""


async def _out(shell: HostShell, argv: list[str]) -> str:
    result = await shell.run(argv, COMMAND_TIMEOUT_S)
    if result.exit_code != 0:
        raise HostUnreadable(f"exit {result.exit_code}: {result.stderr.strip()[:200]}")
    return result.stdout


def _field[T](label: str, read: Callable[[], T]) -> T | None:
    try:
        return read()
    except (HostUnreadable, ValueError, KeyError, IndexError) as error:
        log.warning("host basics: could not read %s: %s", label, error)
        return None


def _keep_mount(fs: str, mount: str, os: str) -> bool:
    if fs in _PSEUDO_FS or fs.startswith("map "):
        return False
    if os == "Darwin" and mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
        return False
    return not (os == "Darwin" and mount.startswith("/System/Volumes/Data/"))


def _disks(df: str, os: str) -> list[DiskUsage]:
    disks, seen = [], set()
    for line in df.splitlines()[1:]:
        row = _DF_ROW.fullmatch(line.strip())
        if row is None:
            continue
        mount = row["mount"]
        if _keep_mount(row["fs"], mount, os) and mount not in seen:
            seen.add(mount)
            disks.append(DiskUsage(mount=mount, used_percent=int(row["pct"])))
    if not disks:
        raise HostUnreadable("no filesystems in df output")
    return disks


def _free(text: str) -> tuple[int, int]:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:":
            return int(parts[1]), int(parts[6])  # total, available
    raise HostUnreadable("no Mem: line")


def _vm_available(text: str) -> int:
    size = re.search(r"page size of (\d+) bytes", text)
    if size is None:
        raise HostUnreadable("no page size")
    pages = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if value.strip().rstrip(".").isdigit():
            pages[key.strip()] = int(value.strip().rstrip("."))
    # free + inactive + speculative + purgeable: what macOS can hand out without swapping
    wanted = ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")
    return sum(pages[k] for k in wanted) * int(size[1])


async def host_basics(shell: HostShell) -> HostBasics:
    """Disk, memory, load and CPUs of a Linux or macOS host. A field it cannot read is None.

    Raises what the shell raises (a lost connection) and `HostUnreadable` when `uname -s` fails;
    a later command that fails only leaves its fields None. Either way the caller keeps what it
    had (the registry its old basics; Verify fails the reading).
    """
    name = (await _out(shell, ["uname", "-s"])).strip()
    if name == "Linux":
        return await _linux(shell)
    if name == "Darwin":
        return await _darwin(shell)
    log.warning("host basics: unsupported os %r", name)
    return HostBasics(os=name)


async def _gather(shell: HostShell, *argvs: list[str]) -> list[str | None]:
    async def one(argv: list[str]) -> str | None:
        try:
            return await _out(shell, argv)
        except HostUnreadable as error:
            log.warning("host basics: %s failed: %s", argv[0], error)
            return None

    return list(await asyncio.gather(*(one(a) for a in argvs)))


async def _linux(shell: HostShell) -> HostBasics:
    df, free, loadavg, nproc = await _gather(
        shell, ["df", "-P"], ["free", "-b"], ["cat", "/proc/loadavg"], ["nproc"]
    )
    mem = _field("free", lambda: _free(free or ""))
    load = _field("loadavg", lambda: [float(x) for x in (loadavg or "").split()[:3]])
    return HostBasics(
        os="Linux",
        disks=_field("df", lambda: _disks(df or "", "Linux")) or [],
        mem_total=mem[0] if mem else None,
        mem_available=mem[1] if mem else None,
        load1=load[0] if load else None,
        load15=load[2] if load and len(load) == 3 else None,
        cpus=_field("nproc", lambda: int((nproc or "").strip())),
    )


async def _darwin(shell: HostShell) -> HostBasics:
    df, vm, sysctl = await _gather(
        shell, ["df", "-P"], ["vm_stat"], ["sysctl", "-n", "hw.memsize", "hw.ncpu", "vm.loadavg"]
    )
    lines = (sysctl or "").splitlines()
    load = _field("vm.loadavg", lambda: [float(x) for x in lines[2].strip("{} \t").split()])
    return HostBasics(
        os="Darwin",
        disks=_field("df", lambda: _disks(df or "", "Darwin")) or [],
        mem_total=_field("hw.memsize", lambda: int(lines[0])),
        mem_available=_field("vm_stat", lambda: _vm_available(vm or "")),
        load1=load[0] if load else None,
        load15=load[2] if load and len(load) == 3 else None,
        cpus=_field("hw.ncpu", lambda: int(lines[1])),
    )


def engine_basics(info: EngineInfo) -> HostBasics:
    """What a socket host can tell: the engine knows CPUs and memory, not disks or load."""
    return HostBasics(
        os=info.os_type or info.operating_system,
        cpus=info.cpus or None,
        mem_total=info.mem_total or None,
    )
