# Spike: Docker over SSH and Coolify labels on real hosts

Measured on 2026-11-02 with [`spike.py`](spike.py). Raw results: [`results-darwin.json`](results-darwin.json), [`results-linux.json`](results-linux.json). Fixtures and labels: [`captures/`](captures/).

**Every number below comes from the saved results of the final run on each host.** Where the text says "earlier runs", the figures were observed while the script was being fixed (3-5 runs per host) and were not saved; they are a rough spread, not evidence.

## Setup

| | M1 test server | Coolify production server |
|---|---|---|
| OS | macOS 27.2 arm64 (engine VM: Docker Desktop, kernel 7.0.14-linuxkit, aarch64) | Ubuntu 24.04.5 LTS, kernel 6.8.0-147, x86_64 |
| Docker | Docker Desktop, engine 29.9.2, API 1.56 | Engine 29.9.2, API 1.56 |
| Login | `dannykukic@MacBook-Pro-M1.local`, key `~/.ssh/id_ed25519` | `root@<coolify-server>` (address left out on purpose), same key |
| Engine socket | `/Users/dannykukic/.docker/run/docker.sock` (there is no `/var/run/docker.sock`) | `/var/run/docker.sock` |
| Non-interactive SSH `PATH` | `/usr/bin:/bin:/usr/sbin:/sbin` (no `docker`) | `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin` (`docker` found) |
| Containers | 0 before the run; one throwaway Compose container started by the script | 10 (9 running), Coolify and real apps |
| Access | read, plus one throwaway container (`--allow-test-container`) | strictly read-only |

The client is the Mac running the spike (Python 3.14, asyncssh, docker SDK 7.x, paramiko), on the same network as the M1 and across the internet from the Linux host. The numbers include that distance.

```bash
uv run docs/spikes/ssh-docker/spike.py dannykukic@MacBook-Pro-M1.local \
  --socket /Users/dannykukic/.docker/run/docker.sock --os darwin --allow-test-container
uv run docs/spikes/ssh-docker/spike.py root@<coolify-server> --display-name '<coolify-server>' \
  --socket /var/run/docker.sock --os linux --log-container coolify-proxy --label-prefix coolify \
  --label-prefix backend-hfys2 --label-prefix frontend- --label-prefix vclhwoxx --label-prefix cr2hzhk4
```

## Mechanism A: asyncssh forwards the socket, the docker SDK talks to the local socket

| Step | M1 (Docker Desktop) | Coolify server (Linux) |
|---|---|---|
| asyncssh connect (3 connects in one run) | 185-275 ms | 193-197 ms |
| `forward_local_path` setup | 0.9 ms | 0.6 ms |
| `ping` (first / second) | 1,485.7 / 11.3 ms (the first call after idle was slow; earlier runs: 21-37 ms, not saved) | 45.7 / 24.2 ms |
| `containers.list(all=True)` | 17.2 ms (0 containers) | 602.2 ms (10 containers) |
| `containers.list(all=True, sparse=True)` | 10.2 ms (0 containers) | 79.6 ms |
| `version()` / `info()` | 21.0 / 22.3 ms | 50.2 / 54.6 ms |
| `logs(tail=50)` | not measured (no container was running when the list ran) | 76.3 ms, 50 lines |
| Events, 30 s window | 11 events, first at 5.27 s (the Compose container created at t=5 s) | 108 events, first at 0.05 s; the window ended on its own at 29.5 s |

Works on both hosts, with no `docker` binary and no `PATH` involved. In earlier runs on the Linux host (not saved) `ping` was 40-51 ms and the non-sparse `containers.list` 577-956 ms.

The Linux stream ran at about 3.6 events per second (108 in 30 s), and all 25 events saved in `events_sample` are `exec_create`, `exec_start` or `exec_die`: the health checks of Coolify's containers and the apps. The `exec_create` and `exec_start` actions carry the whole health-check command after a colon (`exec_create: /bin/sh -c ...`). The M1 stream showed the expected life cycle of one Compose container (see `events_sample` in `results-darwin.json`): `network create`, `container create`, `network connect`, `container start`, `kill`, `stop`, `die`, `destroy`, `network destroy`. The first image pull of `alpine:3` also produced an `image pull` event in an earlier run (not saved).

## Mechanism B: the usual `ssh://` route

| Route | M1 | Coolify server |
|---|---|---|
| `DockerClient("ssh://user@host", use_ssh_client=True)` (the `ssh` CLI) | fails after 221 ms | works, `ping` 797 ms (earlier runs: 753-1,112 ms, not saved) |
| default paramiko route | fails after 207 ms | works, `ping` 454 ms (earlier runs: 425-695 ms, not saved) |
| `ssh host docker system dial-stdio` (what both routes run) | exit 127, stderr `zsh:1: command not found: docker` | exit 0 |

On the M1 both routes raise the same, misleading error:

```
docker.errors.DockerException: Error while fetching server API version: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

The cause only shows in the stderr of the raw command (`command not found: docker`). The SDK has no option to set the remote `docker` path, so a `docker_path` workaround is not possible with it. A per-host workaround would need a `docker` wrapper script on the host, which breaks "install nothing".

## Connection drop (mechanism A)

An events stream (`timeout=None`, no `until`) is open; after 3 s the asyncssh connection is closed (`conn.close()`) or killed (`conn.abort()`).

| | M1 close | M1 abort | Linux close | Linux abort |
|---|---|---|---|---|
| Stream open after 3 s | yes | yes | yes | yes |
| Events received before the drop | 0 | 0 | 12 | 15 |
| How the iterator ends | `StopIteration`, no exception | same | same | same |
| Time from drop to end | 0.8 ms | 1.5 ms | 2.9 ms | 1.4 ms |
| Thread still blocked after 20 s | no | no | no | no |
| `ping()` / `containers.list()` afterwards | `requests.exceptions.ConnectionError: ('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))` | same | same | same |

So a dropped SSH connection does not raise inside the event iterator: the `for` loop just ends, exactly like a normal end. The local socket file is removed with the listener, so every later call fails with `ConnectionError` wrapping `FileNotFoundError`, and the same `DockerClient` is dead.

Not tested: a silent drop (the network path vanishes, no FIN or reset). With `timeout=None` the iterator would block until TCP gives up; asyncssh keepalives (`keepalive_interval`) are the detector.

## Shell commands over the same connection

Each ran as a constant argv over the same asyncssh connection (joined with `shlex.join`; SSH exec takes one string).

| Command | M1 | Linux |
|---|---|---|
| `uname -s` | 29.3 ms | 499.8 ms (the first call after connect; 47-52 ms for the others) |
| `df -P` | 23.8 ms | 52.0 ms |
| `free -b` / `vm_stat` | 24.8 ms | 47.9 ms |
| `cat /proc/loadavg` / `sysctl -n hw.memsize hw.ncpu vm.loadavg` | 22.6 ms | 48.5 ms |
| `nproc` | n/a | 47.2 ms |
| `uptime` | 23.3 ms | 48.0 ms |
| `top -b -n 1` / `top -l 1 -n 15` | 234.6 ms | 287.3 ms |

Details for Task 7:

- `df -P` on Linux lists every container's `overlay` mount, all with the same size and used numbers as `/`. Count one filesystem per device, and prefer `/`.
- macOS `vm_stat` reports a page size of 16384 bytes in its first line; `sysctl vm.loadavg` prints `{ 1.50 2.32 4.75 }`; `hw.memsize` and `hw.ncpu` come out on separate lines in the order asked.
- macOS `uptime` prints `load averages:` (plural), Linux `load average:`.
- `top` prints the process name only (the macOS name is cut at 16 characters, the Linux one at 15 with a trailing `+`), never arguments. The Linux capture keeps 20 lines, as `top -b -n 1 | head -20` would.

## Labels

Full scrubbed label sets are in `captures/labels/`. `scrub_labels` in `spike.py` redacts by key name (secret, token, password, basicauth, ...) and by value shape (bearer and basic-auth headers, `$2y$`-style hashes, `user:pass@` URLs, hex of 40+ characters, base64-like runs of 40+ characters; config hashes, image ids and file paths are exempt). It was checked against a made-up sample of each shape and re-run over every saved file; nothing in these real label sets matched, so nothing was redacted.

| Container | Coolify labels |
|---|---|
| `backend-hfys2...`, `frontend-hfys2...` (app, Coolify 4.5.3) | `coolify.applicationUuid`, `coolify.build-fingerprint`, `coolify.environmentName`, `coolify.managed=true`, `coolify.name`, `coolify.projectName`, `coolify.pullRequestId`, `coolify.resourceName`, `coolify.serviceName`, `coolify.type=application`, `coolify.version` |
| `vclhwoxx...` (app, Coolify 4.5.0) | same, without `build-fingerprint` |
| `cr2hzhk4...` (app, Coolify 4.1.2) | `coolify.applicationId=4` instead of `applicationUuid`; also has `coolify.type=application`; otherwise the same |
| `coolify-proxy` | `coolify.managed=true`, `coolify.proxy=true`, `traefik.enable=false` |
| `coolify-sentinel` | `coolify.managed=true` only |
| `coolify`, `coolify-db`, `coolify-redis` | none; only `com.docker.compose.project=source` |
| M1 Compose container | `com.docker.compose.project`, `.service`, `.container-number`, `.oneoff`, `.config-hash`, `.depends_on`, `.image`, `.version`, `.project.working_dir`, `.project.config_files`, plus the custom label from the compose file |

**How the Coolify versions are known:** each app container carries `coolify.version`, the version of Coolify that deployed it: `4.5.3` on `backend-hfys2...` and `frontend-hfys2...`, `4.5.0` on `vclhwoxx...` (`captures/labels/linux-vclhwoxxy60z4vk70g0vpesq-20261028T194819.json`), `4.1.2` on `cr2hzhk4...` (`captures/labels/linux-cr2hzhk4nvabtad0zinjw1ev-000205002169.json`). The running `coolify` container is the image `coollabsio/coolify:4.5.3`, so the labels are those of the deploying version and an app keeps them until it is redeployed. The older one (4.1.2) **does** carry `coolify.type=application` and `coolify.applicationId=4`, and has no `coolify.applicationUuid`.

Other details:

- The two containers of one Coolify app (`backend` and `frontend`) share `coolify.resourceName=molecula` and `coolify.applicationUuid`, while `coolify.name` and the Compose `service` differ per container. The application UUID is the one key that is shared by both and unique (see the decision doc for the grouping rule).
- Coolify v4.5 apps also carry Traefik labels (router names such as `https-0-<uuid>-backend-b43f`, rules `Host(...)`) and `caddy_*` labels.
- The Compose labels of a Coolify app point at `/artifacts/<id>/docker-compose.coolify.yaml`, and `com.docker.compose.project` equals the application UUID.

## What surprised us

1. **The docker SDK blocks the event loop and deadlocks the tunnel.** `docker.DockerClient(...)` makes a request in its constructor. Called on the event loop that also serves the asyncssh forward, it waited the full 30 s read timeout (observed twice while the script was being fixed; not saved as a result file) and raised `DockerException: Error while fetching server API version: ... Read timed out`. The same happened with a container attribute read (`c.image` does a request). Every SDK call has to run in a worker thread (`asyncio.to_thread`).
2. **A dropped SSH connection does not raise.** The event iterator ends as if nothing had happened (see above).
3. **`containers.list()` costs one request per container.** The SDK inspects each one: 602 ms against 79.6 ms with `sparse=True` for 10 containers.
4. **The M1 failure message hides its cause.** `RemoteDisconnected` instead of `docker: command not found`.
5. **Health checks dominate the event stream** on a Coolify host, and their commands appear in the action text.
6. **Coolify's own `coolify`, `coolify-db` and `coolify-redis` containers have no `coolify.*` labels** at all.
7. **The label for the application id changed between Coolify versions** (`applicationId` in 4.1.2, `applicationUuid` in 4.5.x).
8. Docker Desktop's CLI is not on the non-interactive `PATH`, but its socket answers like any engine's, so mechanism A needs no CLI.

## Writes and limits

- Linux: nothing was written; only listing, label reads, 50 log lines (not saved), event streams and the read commands. No `exec`, no env.
- M1: one throwaway Compose project (`bleepspike`, `alpine:3`, `sleep 40`) was started and removed through the script's `--allow-test-container` flag. It ran several times while the script was being fixed (two earlier runs crashed on bug 1 above and left the container, which was removed by hand with `docker rm -f` and `docker network rm`); the final state is zero containers and no `bleepspike` network. The `alpine:3` image stays pulled.
- The M1 has no container running during the list, so `logs` was not measured there and the sparse list only with 0 containers, and the drop test saw no events before the drop.
- `--allow-test-container` is refused unless `--os darwin`; the Compose step is test-only and never meant for a real host.
- Host keys are not checked in the spike (`known_hosts=None`); the product must pin them.
