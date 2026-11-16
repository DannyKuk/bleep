Run one read-only command on this incident's host to see its disk, memory, load or system journal. Only these exact commands are allowed; anything else is refused (no pipes, no extra arguments, no other programs):
{commands}
journalctl takes a line count (n, max 500) and optionally a systemd unit name. Linux hosts run free, nproc, cat /proc/loadavg, top -b -n 1 and journalctl; macOS hosts run vm_stat, sysctl and top -l 1 -n 15; df, uptime and uname work on both. {untrusted_rule}
