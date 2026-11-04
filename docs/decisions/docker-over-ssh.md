# Docker over SSH: forward the socket through asyncssh

> **Answer:** one asyncssh connection per host. It forwards the remote Docker socket to a local unix socket (`conn.forward_local_path`), the `docker` SDK talks to that local socket, and the same connection runs the allowlisted shell commands. It worked on both hosts, including the macOS Docker Desktop host where the usual `ssh://` route fails because `docker` is not on the non-interactive `PATH`. `docker_socket` defaults to `/var/run/docker.sock` on Linux and needs per-host config for Docker Desktop (`/Users/<user>/.docker/run/docker.sock`). Services are keyed by `coolify.applicationUuid`, else a prefixed `coolify.applicationId`, else the compose project, else the container name, and shown under `coolify.resourceName` (this replaces the plan's `resourceName` then `applicationId` order, because `resourceName` is not unique). A dropped connection does **not** raise: the event iterator just ends, so Task 4 must treat any end of the stream that it did not ask for as a drop and reconnect with a new connection, forward and client.

> **Update (2026-11-02, #13):** the watcher does not hold the event stream open. It pulls: every 15 s per host it reconnects if needed, reads `events(since=cursor, until=now)` and lists containers. Reason: with agentless SSH to a few hosts, some of which sleep (a laptop, a spare Mac), a dropped link is normal and a pull catches up from the cursor with no stream state to repair. The cost is up to 15 s of detection delay and one listing per tick per host; at hundreds of hosts an agent on each host would be the right design. The stream notes below stay as evidence for the connection behaviour. `SshSession.open()` now also has an outer timeout, so a host that vanished without a FIN fails a tick fast.

## Question

How does Bleep talk to the Docker engine of a host it can only reach over SSH with a key, with nothing installed on the host? The way must:
- work on Linux servers (the Coolify hosts) **and** on a Mac with Docker Desktop (the test machine);
- use one connection for Docker and for host shell commands, so reconnecting happens in one place;
- end its event stream in a way the watcher can detect.

## What we tried

Both on 2026-11-02 (figures are from the saved results of the final run; ranges from earlier runs are marked as not saved), with [`spike.py`](../spikes/ssh-docker/spike.py), details in the [report](../spikes/ssh-docker/report.md):
- **A, socket forward:** `asyncssh.connect`, `forward_local_path(local, remote_socket)`, `docker.DockerClient("unix://local")`: ping, list, 50 log lines, a 30 s event stream.
- **B, the usual route:** `DockerClient("ssh://user@host")` with `use_ssh_client=True` and with paramiko.
- **Connection drop:** `close()` and `abort()` of the connection during an open event stream.
- **Shell commands** over the same connection, saved as fixtures for Task 7.
- **Labels** of Coolify apps and of Coolify's own containers.

Hosts: an M1 Mac (macOS 27.2, Docker Desktop 29.9.2) and a production Coolify server (Linux 6.8, Docker 29.9.2), the latter strictly read-only.

## Result

| | M1 (Docker Desktop) | Coolify server (Linux) |
|---|---|---|
| A: forward + ping | works; first call 1,486 ms after idle (earlier runs 21-37 ms, not saved), 11 ms after | works, 46 ms first, 24 ms after |
| A: `containers.list(all=True)` | 17 ms (0 containers) | 602 ms (10 containers); 80 ms with `sparse=True` |
| A: 30 s events | works (11 events, the life cycle of one container) | works (108 events, health-check `exec_*` in the sample) |
| B: `ssh://`, `use_ssh_client=True` | **fails**, `docker: command not found` on the remote | works, 797 ms for a ping (earlier runs 753-1,112 ms, not saved) |
| B: `ssh://`, paramiko | **fails**, same | works, 454 ms for a ping (earlier runs 425-695 ms, not saved) |
| Remote `PATH` has `docker` | no (`/usr/bin:/bin:/usr/sbin:/sbin`) | yes |
| Engine socket | `/Users/dannykukic/.docker/run/docker.sock`; no `/var/run/docker.sock` | `/var/run/docker.sock` |
| asyncssh connect | 185-275 ms | 193-197 ms |

Dropping the connection during an open event stream:

| | Result (same on both hosts, `close()` and `abort()`) |
|---|---|
| Event iterator | ends with `StopIteration`, no exception, 0.8-2.9 ms after the drop |
| Later calls on the same client | `requests.exceptions.ConnectionError: ('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))` |

## What it shows

- **A works everywhere; B does not.** On the Mac the SDK's `ssh://` route runs `docker system dial-stdio` through the remote shell, which finds no `docker`. The SDK reports it as `RemoteDisconnected`, and it has no setting for a remote `docker` path. A is independent of the remote `PATH`: the forward only needs the socket path.
- **A is also cheaper per call.** After one 200 ms connect, a ping is 24 ms on the Linux host. B's ping took 454 and 797 ms there (paramiko, ssh CLI).
- **One connection serves both jobs.** The shell commands ran over the same asyncssh connection in 23-52 ms each, `top` 235-287 ms, and the first Linux call after connect 500 ms, so reconnect logic lives in one place.
- **The drop is silent.** Because the iterator ends without an exception, an exception handler alone would never notice a lost host.
- **The SDK must never run on the event loop.** The forward is served by the same loop. The constructor and every call block, so called directly they wait for their read timeout and fail (observed in an earlier run, not saved: 30 s, then `Error while fetching server API version`).
- **Coolify labels are consistent but not uniform.** All app containers carry `coolify.resourceName`; the id label differs by Coolify version; Coolify's own containers carry no `coolify.*` labels (see the report).

## Decided along the way

- **Mechanism: A (asyncssh forward).** `ssh://` plus a per-host `docker_path` was the plan's fallback and is not needed; it also could not work, because the SDK has no such option.
- **`docker_socket` per host.** Default `/var/run/docker.sock` (Linux). On Docker Desktop for Mac the path is `/Users/<user>/.docker/run/docker.sock` and goes into the host's config. A missing `/var/run/docker.sock` on a Mac is expected, not a fault.
- **All SDK calls in worker threads** (`asyncio.to_thread`), including `DockerClient(...)` and attribute access that loads data (`container.image`). Use `containers.list(sparse=True)` and read what is needed from the listing; the default does one inspect per container.
- **Events: `timeout=None`, no `until`.** The default read timeout is 60 s and a quiet host sends nothing. Ending the stream is done by closing the connection, which stopped the reader thread within 3 ms (0.8-2.9 ms measured).
- **Reconnect builds everything again.** After a drop the local socket file is gone, so the old `DockerClient` is useless: open a new asyncssh connection, a new forward (a new temp socket path) and a new `DockerClient`.
- **Detect silent drops with asyncssh keepalives** (`keepalive_interval`, `keepalive_count_max`), so a vanished network path closes the connection and ends the stream instead of blocking it.
- **Shell commands are built from constant argv lists joined with `shlex.join`.** SSH exec takes one string; the allowlist in Task 7 must quote every argument and take no free-form input. Remote shells differ (the Mac's is zsh).
- **Task 7 parses what the captures show**, in [`captures/`](../spikes/ssh-docker/captures/): `df -P` on Linux repeats the root filesystem once per container `overlay` mount, so count a device once and prefer `/`; macOS needs `vm_stat` (16384-byte pages here) and `sysctl -n hw.memsize hw.ncpu vm.loadavg`, whose load comes as `{ 1.50 2.32 4.75 }`; `top` shows process names, not arguments.
- **Events: filter `exec_*`.** They made up every event in the saved sample of a Coolify host's stream (health checks, 108 events in 30 s), and the action text carries the whole command line. Keep only the event types that matter (container `start`, `die`, `kill`, `oom`, `stop`, `destroy`, `health_status`) and never store or show the action text of `exec_*`.
- **Host keys:** the spike skipped checking (`known_hosts=None`); the product pins them.

### What Task 4 must handle on a connection drop

1. The event iterator ends **without an exception**. `stream_events` treats every end it did not request as a drop. A deliberate stop (shutdown, host removed) sets a stop flag **before** closing the connection, so it is not mistaken for a drop. The same applies to any SDK exception (`requests.exceptions.ConnectionError` wrapping `FileNotFoundError` once the socket file is gone).
2. Check `conn.is_closed()` (or catch `asyncssh.Error`, `OSError`) to tell the cause; the response is the same: close what is left, then build a new connection, forward and client.
3. Resuming events with `since=<time of the last event seen>` so nothing is missed is a **proposal that was not tested** here; Task 4 verifies it.
4. Connect with `keepalive_interval=15` and `keepalive_count_max=3`, which bounds the detection of a silent drop at about 45 s. These values were not measured; the silent-drop case itself was not tested.
5. Mark the host unreachable only after the reconnect attempts fail, with backoff; do not open an incident for one drop.
6. Run `ping`, `list` and the event iterator in threads; never construct the client on the loop.

**Acceptance test for Task 4:** a silent drop (the network path vanishes without a reset) is detected by the keepalive within the 45 s bound, the event stream ends, and the watcher reconnects.

### Coolify labels: the grouping rule for Task 5

Keys found on real application containers (saved in [`captures/labels/`](../spikes/ssh-docker/captures/labels/)):

| Label | Seen on | Meaning |
|---|---|---|
| `coolify.applicationUuid` | 4.5.0, 4.5.3 | unique id of the application |
| `coolify.applicationId` | 4.1.2 (`4`) | numeric id, instead of the UUID on this older version |
| `coolify.resourceName` | all three | the application's name (`molecula`, `dkwebsite`); not unique |
| `coolify.type` | all three (`application`) | resource type; the 4.1.2 container carries it too |
| `coolify.pullRequestId` | all three (`0`) | preview deployments have another value (not seen on this host) |
| `coolify.managed`, `coolify.name`, `coolify.serviceName`, `coolify.projectName`, `coolify.environmentName`, `coolify.version` | all three | context and display |

Each container's version is its own `coolify.version` label (the Coolify that deployed it); the running Coolify is 4.5.3. The labels stay as deployed until the app is redeployed.

**The rule:**

- **Service key:** `coolify.applicationUuid`, else `coolify.applicationId` prefixed (for example `id:4`) so ids cannot collide with UUIDs, else `compose:<com.docker.compose.project>`, else `container:<name>`. The compose and container keys are namespaced (#5) so a single container `app`, a compose project `app` and a project named like a UUID cannot merge; display names stay unprefixed.
- **Display name:** `coolify.resourceName` when present, else the key.
- **Coolify app membership:** `coolify.type == "application"` when the label exists. On a version without `coolify.type`, a container with `coolify.applicationId` counts. (The saved 4.1.2 labels do carry `coolify.type`, so this only covers versions we have not seen.)
- **Preview deployments:** `coolify.pullRequestId` present and not `"0"` makes their own service, keyed `<uuid>-pr-<n>`.
- **Coolify's own containers** (`coolify`, `coolify-db`, `coolify-redis`, `coolify-sentinel`, `coolify-proxy`) are not excluded. They have no application labels, so they fall back to the compose project (`source`, `coolify-proxy`), else the container name. `coolify-sentinel` has only `coolify.managed`, and no compose project, so it is keyed by its name.

**This replaces the plan's `coolify.resourceName`, then `coolify.applicationId`, order.** `resourceName` is a name, not an id: two apps in different projects or environments can share it, and it would merge their incidents. The UUID is unique and is on every 4.5.x container, while `applicationId` exists only on older ones. In the saved data `backend` and `frontend` of one app share both `applicationUuid` and `resourceName` (`molecula`), so the UUID key still groups them together.

## Limits

- **Two hosts, one Docker version (29.9.2).** Older engines, rootless Docker and Podman sockets were not tried.
- **Coolify databases, one-click services and preview deployments were not on the host.** Their label sets (`coolify.type` other than `application`, `pullRequestId` other than 0) are unverified; Task 5 should group them by the same fallbacks and the compose project.
- **The compose step is test-only.** `--allow-test-container` writes to the host and the script refuses it unless `--os darwin`; it is never part of the product.
- **No silent-drop test.** Only `close()` and `abort()` were tried; a network that vanishes without a reset was not.
- **No container logs on the M1, and the sparse list only with 0 containers,** because it had no container running during the list.
- **Latency depends on distance.** The Linux numbers include the path from the Mac running the spike to the server; they are a floor for one measurement, not a promise.
- **The drop test had 0 to 15 events before the drop,** not a stream cut in the middle of a large event.

## Reproduce

```bash
# M1 / Docker Desktop (writes: starts and removes one throwaway Compose container)
uv run docs/spikes/ssh-docker/spike.py <user>@<mac> --socket /Users/<user>/.docker/run/docker.sock \
  --os darwin --allow-test-container

# Linux host (read-only)
uv run docs/spikes/ssh-docker/spike.py <user>@<host> --display-name '<name for results>' --socket /var/run/docker.sock --os linux \
  --label-prefix coolify --label-prefix backend-

ssh <user>@<mac> 'echo $PATH; docker --version'   # why B fails on a Mac
```

The script writes `results-<os>.json` and the `captures/` files next to itself.
