# /// script
# requires-python = ">=3.12"
# dependencies = ["asyncssh>=2.14", "docker>=7.1", "paramiko>=3.4"]
# ///
"""Spike: reach a Docker engine over SSH without installing anything on the host.

    uv run docs/spikes/ssh-docker/spike.py <user@host> --socket PATH [--os linux|darwin]

Read-only on the remote host. The only write is behind --allow-test-container (a throwaway
Compose container on a test machine). Env vars of containers are never read or printed.
"""

import argparse
import asyncio
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import asyncssh
import docker

HERE = Path(__file__).parent
SECRET_KEY = re.compile(r"(secret|token|password|passwd|basicauth|apikey|api_key|private)", re.I)
DOCKER_CLI_DARWIN = "/Applications/Docker.app/Contents/Resources/bin/docker"

COMMANDS = {
    "linux": {
        "uname": ["uname", "-s"],
        "df": ["df", "-P"],
        "free": ["free", "-b"],
        "loadavg": ["cat", "/proc/loadavg"],
        "nproc": ["nproc"],
        "uptime": ["uptime"],
        "top": ["top", "-b", "-n", "1"],
    },
    "darwin": {
        "uname": ["uname", "-s"],
        "df": ["df", "-P"],
        "vm_stat": ["vm_stat"],
        "sysctl": ["sysctl", "-n", "hw.memsize", "hw.ncpu", "vm.loadavg"],
        "uptime": ["uptime"],
        "top": ["top", "-l", "1", "-n", "15"],
    },
}

COMPOSE_YAML = """\
name: bleepspike
services:
  probe:
    image: alpine:3
    command: ["sleep", "40"]
    labels:
      bleep.spike: "true"
"""


def ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


SECRET_VALUE = re.compile(
    r"(bearer\s+\S+|basic\s+[A-Za-z0-9+/=]{8,}|\$2[abxy]\$\d\d\$\S+|\$apr1\$\S+"
    r"|://[^/\s:@]+:[^/\s@]+@|\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9]))",
    re.I,
)
# Config hashes, image ids and file paths are long but not secrets.
PUBLIC_VALUE_KEYS = (
    "com.docker.compose.config-hash",
    "com.docker.compose.image",
    "coolify.build-fingerprint",
    "com.docker.compose.project.config_files",
    "com.docker.compose.project.environment_file",
    "com.docker.compose.project.working_dir",
)


def scrub_labels(labels: dict[str, str]) -> dict[str, str]:
    """Redact values by key name and by what the value looks like."""
    out = {}
    for key, value in sorted(labels.items()):
        by_key = SECRET_KEY.search(key)
        by_value = key not in PUBLIC_VALUE_KEYS and SECRET_VALUE.search(value)
        out[key] = "<redacted>" if by_key or by_value else value
    return out


def trim_top(text: str, max_lines: int | None) -> str:
    """top prints the process name only, never arguments, so the raw table is safe to keep."""
    lines = text.splitlines()
    return "\n".join(lines[:max_lines] if max_lines else lines) + "\n"


class Spike:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.user, _, self.host = args.target.rpartition("@")
        self.results: dict = {"target": args.display_name or args.target, "os": args.os, "socket": args.socket}

    async def connect(self) -> asyncssh.SSHClientConnection:
        start = time.perf_counter()
        conn = await asyncssh.connect(
            self.host,
            username=self.user or None,
            client_keys=[os.path.expanduser(self.args.key)],
            known_hosts=None,  # spike only: the product pins host keys
            connect_timeout=15,
        )
        self.results.setdefault("connect_ms", []).append(ms(start))
        return conn

    async def forward(self, conn, tmp: str, name: str = "docker.sock"):
        local = os.path.join(tmp, name)
        start = time.perf_counter()
        listener = await conn.forward_local_path(local, self.args.socket)
        return local, listener, ms(start)

    async def mechanism_a(self, conn, tmp: str) -> None:
        res: dict = {}
        local, _listener, res["forward_setup_ms"] = await self.forward(conn, tmp)
        client = await asyncio.to_thread(docker.DockerClient, base_url=f"unix://{local}", timeout=30)

        async def timed(name, fn):
            start = time.perf_counter()
            try:
                value = await asyncio.to_thread(fn)
                res[name + "_ms"] = ms(start)
                return value
            except Exception as exc:  # noqa: BLE001 - spike records anything
                res[name + "_error"] = f"{type(exc).__name__}: {exc}"
                return None

        await timed("ping", client.ping)
        await timed("ping_second", client.ping)
        containers = await timed("containers_list", lambda: client.containers.list(all=True))
        res["container_count"] = len(containers or [])
        await timed("containers_list_sparse", lambda: client.containers.list(all=True, sparse=True))
        version = await timed("version", client.version)
        info = await timed("info", client.info)
        res["engine"] = {
            "server_version": (version or {}).get("Version"),
            "api_version": (version or {}).get("ApiVersion"),
            "engine_os": (info or {}).get("OperatingSystem"),
            "kernel": (info or {}).get("KernelVersion"),
            "arch": (info or {}).get("Architecture"),
        }
        if containers:
            running = [c for c in containers if c.status == "running"] or containers
            pick = next((c for c in running if c.name == self.args.log_container), running[0])
            logs = await timed("logs_50", lambda: pick.logs(tail=50))
            res["log_lines"] = len((logs or b"").splitlines())
            await asyncio.to_thread(self.dump_labels, containers)
        await self.events(conn, client, res)
        self.results["mechanism_a"] = res
        client.close()

    def dump_labels(self, containers) -> None:
        out = HERE / "captures" / "labels"
        out.mkdir(parents=True, exist_ok=True)
        for c in containers:
            wanted = any(c.name.startswith(p) for p in self.args.label_prefix)
            if wanted or c.labels.get("com.docker.compose.project") == "bleepspike":
                (out / f"{self.args.os}-{c.name}.json").write_text(
                    json.dumps(
                        {"container": c.name, "image": c.image.tags[:1], "labels": scrub_labels(c.labels)},
                        indent=2,
                    )
                    + "\n"
                )

    async def events(self, conn, client, res: dict) -> None:
        """Stream events for N seconds; optionally start the throwaway container mid-way."""
        seen: list[tuple[float, str, str]] = []
        t0 = time.perf_counter()
        stop = threading.Event()

        def reader() -> None:
            stream = client.events(decode=True, since=int(time.time()), until=int(time.time()) + self.args.seconds)
            for ev in stream:
                seen.append((round(time.perf_counter() - t0, 2), ev.get("Type", ""), ev.get("Action", "").split(":")[0]))
            stop.set()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        if self.args.allow_test_container:
            await asyncio.sleep(5)
            res["test_container_up"] = await self.compose(conn, "up", "-d")
            await asyncio.sleep(3)
            await asyncio.to_thread(lambda: self.dump_labels(client.containers.list(all=True)))
            await asyncio.sleep(5)
            res["test_container_down"] = await self.compose(conn, "down")
        await asyncio.to_thread(thread.join, self.args.seconds + 10)
        res["events_window_s"] = self.args.seconds
        res["events_count"] = len(seen)
        res["events_first_s"] = seen[0][0] if seen else None
        res["events_sample"] = [list(e) for e in seen[:25]]
        res["events_ended_cleanly"] = stop.is_set()
        res["events_elapsed_s"] = round(time.perf_counter() - t0, 1)

    async def compose(self, conn, *verb: str) -> dict:
        cli = DOCKER_CLI_DARWIN if self.args.os == "darwin" else "docker"
        path = "PATH=/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        argv = ["env", path, cli, "compose", "-p", "bleepspike", "-f", "-", *verb]
        start = time.perf_counter()
        r = await conn.run(shlex.join(argv), input=COMPOSE_YAML, check=False)
        return {"exit": r.exit_status, "ms": ms(start), "stderr_tail": (r.stderr or "")[-300:]}

    async def drop(self, mode: str) -> dict:
        """Kill the SSH connection while an events stream is open and watch what the SDK does."""
        res: dict = {"mode": mode}
        with tempfile.TemporaryDirectory() as tmp:
            conn = await self.connect()
            local, _l, _ = await self.forward(conn, tmp)
            client = await asyncio.to_thread(docker.DockerClient, base_url=f"unix://{local}", timeout=None)
            await asyncio.to_thread(client.ping)
            state: dict = {"events": 0}
            t0 = time.perf_counter()

            def reader() -> None:
                try:
                    for _ev in client.events(decode=True):
                        state["events"] += 1
                    state["end"] = "iterator ended (StopIteration), no exception"
                except Exception as exc:  # noqa: BLE001
                    state["end"] = f"raised {type(exc).__module__}.{type(exc).__name__}: {str(exc)[:200]}"
                state["ended_at"] = time.perf_counter()

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            await asyncio.sleep(3)
            res["stream_open_after_3s"] = thread.is_alive()
            dropped = time.perf_counter()
            if mode == "abort":
                conn.abort()
            else:
                conn.close()
            await asyncio.to_thread(thread.join, 20)
            res["thread_still_blocked_after_20s"] = thread.is_alive()
            res["events_before_drop"] = state["events"]
            res["end"] = state.get("end", "still blocked")
            if "ended_at" in state:
                res["ended_after_drop_ms"] = round((state["ended_at"] - dropped) * 1000, 1)
            for name, fn in (("ping", client.ping), ("containers_list", client.containers.list)):
                try:
                    await asyncio.to_thread(fn)
                    res[f"{name}_after_drop"] = "ok"
                except Exception as exc:  # noqa: BLE001
                    res[f"{name}_after_drop"] = f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:160]}"
            res["total_s"] = round(time.perf_counter() - t0, 1)
        return res

    def mechanism_b(self) -> dict:
        res: dict = {}
        for name, kwargs in (("ssh_cli", {"use_ssh_client": True}), ("paramiko", {"use_ssh_client": False})):
            start = time.perf_counter()
            try:
                client = docker.DockerClient(base_url=f"ssh://{self.args.target}", timeout=30, **kwargs)
                client.ping()
                res[name] = {"ok": True, "ping_ms": ms(start), "containers": len(client.containers.list())}
                client.close()
            except Exception as exc:  # noqa: BLE001
                res[name] = {"ok": False, "ms": ms(start), "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        start = time.perf_counter()
        try:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", self.args.target, "docker", "system", "dial-stdio"],
                input=b"", capture_output=True, timeout=20, check=False,
            )
            res["dial_stdio_raw"] = {"exit": r.returncode, "ms": ms(start), "stderr": r.stderr.decode()[:200]}
        except subprocess.TimeoutExpired:
            res["dial_stdio_raw"] = {"error": "timeout"}
        return res

    async def shell(self, conn) -> dict:
        res: dict = {}
        out = HERE / "captures" / self.args.os
        out.mkdir(parents=True, exist_ok=True)
        for name, argv in COMMANDS[self.args.os].items():
            start = time.perf_counter()
            r = await conn.run(shlex.join(argv), check=False)
            text = r.stdout or ""
            if name == "top":
                text = trim_top(text, 20 if self.args.os == "linux" else None)
            (out / f"{name}.txt").write_text(text)
            res[name] = {"ms": ms(start), "exit": r.exit_status, "bytes": len(text), "stderr": (r.stderr or "")[:120]}
        start = time.perf_counter()
        r = await conn.run("docker --version", check=False)
        res["remote_docker_on_path"] = {"exit": r.exit_status, "ms": ms(start), "stderr": (r.stderr or "")[:120]}
        r = await conn.run("echo $PATH", check=False)
        res["remote_path"] = (r.stdout or "").strip()
        return res

    async def run(self) -> dict:
        conn = await self.connect()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.results["shell"] = await self.shell(conn)
                self.results["mechanism_b"] = await asyncio.to_thread(self.mechanism_b)
                await self.mechanism_a(conn, tmp)
            finally:
                conn.close()
        self.results["drop"] = [await self.drop("close"), await self.drop("abort")]
        return self.results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("target", help="user@host")
    p.add_argument("--socket", required=True, help="remote Docker socket path")
    p.add_argument("--os", choices=["linux", "darwin"], default="linux")
    p.add_argument("--display-name", default="", help="name written to results instead of the real host")
    p.add_argument("--key", default="~/.ssh/id_ed25519")
    p.add_argument("--seconds", type=int, default=30, help="length of the events window")
    p.add_argument("--log-container", default="", help="container to read 50 log lines from")
    p.add_argument("--label-prefix", action="append", default=[], help="dump labels of containers with this name prefix")
    p.add_argument("--allow-test-container", action="store_true", help="WRITES: start and remove one throwaway Compose container (test machines only)")
    args = p.parse_args()
    if args.allow_test_container and args.os != "darwin":
        p.error("--allow-test-container is for the macOS test machine only (--os darwin)")
    results = asyncio.run(Spike(args).run())
    print(json.dumps(results, indent=2))
    (HERE / f"results-{args.os}.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
