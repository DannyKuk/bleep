import pytest

from bleep.hosts.shell import ALLOWED, CommandNotAllowed, engine_basics, host_basics, run_allowed
from bleep.hosts.ssh import CommandResult, SshUnavailable
from bleep.schemas.docker import EngineInfo
from fakes import FakeShell

REFUSED = [
    "rm -rf /",
    "df -P; reboot",
    "df -P && id",
    "journalctl -n 10 -u x;id",
    "cat /etc/shadow",
    "docker restart x",
    "df -P $(id)",
    "uname -s; id",
    "df -P|cat",
    "df -P > /tmp/x",
    "df -P *",
    "FOO=1 df -P",
    "/bin/df -P",
    "df -P `id`",
    "df",
    "df -P -h",
    "journalctl -n 501 --no-pager",
    "journalctl -n 10",
    "journalctl -n 10 --no-pager -u 'a b'",
    "journalctl -n 10 --no-pager -u x;id",
    "journalctl -n 10 --no-pager -u -f",
    "journalctl -n 0 --no-pager",
    "journalctl -n -5 --no-pager",
    "cat /proc/loadavg /etc/shadow",
    "df -P 'unterminated",
    "",
    "   ",
]


@pytest.mark.parametrize("cmd", REFUSED)
async def test_disallowed_commands_refused(cmd):
    shell = FakeShell()
    with pytest.raises(CommandNotAllowed) as error:
        await run_allowed(shell, cmd, "Linux")
    assert "df -P" in str(error.value)  # the message lists what is allowed
    assert shell.calls == []  # nothing was run


@pytest.mark.parametrize(
    ("cmd", "os"),
    [
        ("journalctl -n 10 --no-pager", "Darwin"),
        ("vm_stat", "Linux"),
        ("free -b", "Darwin"),
        ("nproc", "Darwin"),
        ("cat /proc/loadavg", "Darwin"),
        ("top -l 1 -n 15", "Linux"),
        ("top -b -n 1", "Darwin"),
        ("sysctl -n hw.memsize hw.ncpu vm.loadavg", "Linux"),
    ],
)
async def test_command_of_the_other_os_is_refused_with_a_reason(cmd, os):
    shell = FakeShell()
    with pytest.raises(CommandNotAllowed, match=os):
        await run_allowed(shell, cmd, os)
    assert shell.calls == []


@pytest.mark.parametrize(
    ("cmd", "argv"),
    [
        ("uname -s", ["uname", "-s"]),
        ("df -P", ["df", "-P"]),
        ("free -b", ["free", "-b"]),
        ("cat /proc/loadavg", ["cat", "/proc/loadavg"]),
        ("nproc", ["nproc"]),
        ("uptime", ["uptime"]),
        ("top -b -n 1", ["top", "-b", "-n", "1"]),
        ("journalctl -n 500 --no-pager", ["journalctl", "-n", "500", "--no-pager"]),
        ("journalctl -n 1 --no-pager -u ssh", ["journalctl", "-n", "1", "--no-pager", "-u", "ssh"]),
        (
            "journalctl -n 20 --no-pager -u docker@x.service",
            ["journalctl", "-n", "20", "--no-pager", "-u", "docker@x.service"],
        ),
        ("  df   -P ", ["df", "-P"]),
    ],
)
async def test_allowed_linux_commands_run_as_an_argv_list(cmd, argv):
    shell = FakeShell("linux", {tuple(argv): "out"})
    assert await run_allowed(shell, cmd, "Linux") == "out"
    assert shell.calls == [argv]


async def test_darwin_commands_run():
    shell = FakeShell("darwin")
    assert "page size of 16384" in await run_allowed(shell, "vm_stat", "Darwin")
    assert (await run_allowed(shell, "top -l 1 -n 15", "Darwin")).startswith("Processes:")
    assert shell.calls == [["vm_stat"], ["top", "-l", "1", "-n", "15"]]


async def test_failing_command_returns_its_exit_code_and_stderr():
    shell = FakeShell(
        "linux",
        {
            ("journalctl", "-n", "5", "--no-pager"): CommandResult(
                exit_code=1, stdout="", stderr="no"
            )
        },
    )
    out = await run_allowed(shell, "journalctl -n 5 --no-pager", "Linux")
    assert "exit 1" in out and "no" in out


def test_allowed_is_a_table_of_argv_templates():
    assert ALLOWED["df"] == ["df", "-P"]
    assert all(isinstance(t, list) and all(isinstance(a, str) for a in t) for t in ALLOWED.values())


async def test_linux_basics_parse_from_the_real_captures():
    b = await host_basics(FakeShell("linux"))
    assert b.os == "Linux"
    assert [(d.mount, d.used_percent) for d in b.disks] == [("/", 49), ("/boot/efi", 1)]
    assert b.cpus == 2
    assert b.mem_total == 4005433344
    assert b.mem_available == 2293391360
    assert (b.load1, b.load15) == (0.44, 0.30)


async def test_darwin_basics_parse_from_the_real_captures():
    b = await host_basics(FakeShell("darwin"))
    assert b.os == "Darwin"
    assert [(d.mount, d.used_percent) for d in b.disks] == [
        ("/", 4),
        ("/System/Volumes/Data", 7),
        ("/Volumes/Recovery", 1),
    ]
    assert b.mem_total == 17179869184
    # (free + inactive + speculative + purgeable) pages * 16384 bytes
    assert b.mem_available == (101278 + 387748 + 1995 + 29369) * 16384 == 8526069760
    assert b.cpus == 8
    assert (b.load1, b.load15) == (1.15, 3.61)


async def test_uname_runs_first():
    shell = FakeShell("linux")
    await host_basics(shell)
    assert shell.calls[0] == ["uname", "-s"]
    assert all(call[0] != "journalctl" for call in shell.calls)


async def test_pseudo_filesystems_and_duplicate_overlays_are_dropped():
    df = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "tmpfs 10 1 9 10% /run\n"
        "devtmpfs 10 1 9 10% /dev\n"
        "shm 10 1 9 10% /var/lib/docker/containers/x/mounts/shm\n"
        "squashfs 10 10 0 100% /snap/core/1\n"
        "overlay 10 5 5 50% /var/lib/docker/rootfs/a\n"
        "overlay 10 5 5 50% /var/lib/docker/rootfs/b\n"
        "/dev/sda1 100 90 10 90% /\n"
        "/dev/sdb1 100 20 80 20% /mnt/my data\n"
    )
    shell = FakeShell("linux", {("df", "-P"): df})
    b = await host_basics(shell)
    assert [(d.mount, d.used_percent) for d in b.disks] == [("/", 90), ("/mnt/my data", 20)]


async def test_one_bad_field_stays_none_and_does_not_raise(caplog):
    shell = FakeShell(
        "linux",
        {("free", "-b"): "garbage", ("nproc",): CommandResult(exit_code=1, stdout="", stderr="x")},
    )
    b = await host_basics(shell)
    assert b.mem_total is None and b.mem_available is None and b.cpus is None
    assert b.disks and b.load1 == 0.44
    assert "free" in caplog.text


async def test_darwin_bad_vm_stat_leaves_available_none():
    shell = FakeShell("darwin", {("vm_stat",): "nonsense"})
    b = await host_basics(shell)
    assert b.mem_available is None and b.mem_total == 17179869184


async def test_unknown_os_gives_only_the_os():
    b = await host_basics(FakeShell("linux", {("uname", "-s"): "FreeBSD\n"}))
    assert b.os == "FreeBSD" and b.disks == [] and b.cpus is None


async def test_lost_connection_propagates():
    shell = FakeShell("linux", {("uname", "-s"): SshUnavailable("a", "gone")})
    with pytest.raises(SshUnavailable):
        await host_basics(shell)


def test_engine_basics_has_no_disks():
    b = engine_basics(EngineInfo(os_type="linux", cpus=4, mem_total=8_000_000_000))
    assert b.disks == [] and b.load1 is None and b.load15 is None and b.mem_available is None
    assert (b.os, b.cpus, b.mem_total) == ("linux", 4, 8_000_000_000)


def test_engine_basics_falls_back_to_operating_system():
    assert engine_basics(EngineInfo(operating_system="Docker Desktop")).os == "Docker Desktop"
