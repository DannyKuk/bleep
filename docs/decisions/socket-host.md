# Socket host: a filtered proxy, not a raw socket mount

> **Answer:** the demo host reaches the local Docker engine through `tecnativa/docker-socket-proxy` (pinned to v0.5.0) running a small allowlist, not through a raw `/var/run/docker.sock` mount in the backend. A raw mount is root on the machine: whoever can talk to the socket can start a privileged container with the host's filesystem mounted. The proxy's own environment flags could not express "restart yes, create no": `POST=1` is the only way to let any write through, and it then allows every write under `/containers` that `CONTAINERS=1` opens, so create, exec and delete went through when measured. A mounted template replaces the proxy's rules with four: read containers, events, info, version and ping; start, stop and restart of one named container; nothing else. Measured on Docker Desktop 4.95: all of those worked, and create, exec, delete, kill, image pull, volume create, network create and file copy-out were refused with 403.

## Question

Bleep needs to watch the containers of the machine it runs on (the `demo` profile) and restart one when asked. How does the backend get at that Docker engine without being handed root on the machine?

## What we tried

- **A raw mount** (`/var/run/docker.sock:/var/run/docker.sock` in the backend). Simple and complete, and the engine cannot tell Bleep from root. A bug or a prompt injection in the backend would be a bug on the host. Not used.
- **The proxy with its flags**, as the task first described: `CONTAINERS=1 EVENTS=1 INFO=1 PING=1 VERSION=1 ALLOW_START=1 ALLOW_STOP=1 ALLOW_RESTARTS=1`.
  - Without `POST=1`, restart was refused with 403: the stock config denies every non-GET request first.
  - With `POST=1`, restart worked, and so did `POST /containers/create`, `POST /containers/<id>/exec` and `DELETE /containers/<id>`, because `CONTAINERS=1` allows any path that starts with `/containers`.
- **The proxy with its own rules** (`config/socket-proxy.cfg.template`, mounted over the image's template, so the pinned image stays untouched). It allows a POST only when the path is `/containers/<name>/(start|stop|restart)`, then denies any other non-GET, denies reads that copy data out of a container (`archive`, `export`, `attach`), allows reads under `/containers`, `/events`, `/info`, `/version`, `/_ping`, and denies the rest. The proxy's environment flags are not used.

The calls were made from inside the backend container with urllib against `http://socket-proxy:2375`, with a throwaway `alpine sleep 300` container as the target.

## Result

Stock flags, with `POST=1`:

| Call | Status |
|---|---|
| `POST /containers/<id>/restart`, `stop`, `start` | 204 |
| `POST /containers/create` | 201 (a container was created) |
| `POST /containers/<id>/exec` | 201 |
| `DELETE /containers/<id>?force=1` | 204 (the target was deleted) |
| `POST /images/create`, `/volumes/create`, `/networks/create` | 403 |

Own rules (the shipped config):

| Call | Status |
|---|---|
| `GET /_ping`, `/version`, `/info`, `/containers/json`, `/containers/<id>/json`, `/containers/<id>/logs`, `/events` | 200 |
| `GET /v1.45/containers/json` (versioned path) | 200 |
| `POST /containers/<id>/restart`, `stop`, `start` | 204 |
| `POST /containers/create`, also versioned | 403 |
| `POST /containers/<id>/exec` | 403 |
| `DELETE /containers/<id>?force=1` | 403 |
| `POST /containers/<id>/kill` | 403 |
| `POST /images/create` (pull) | 403 |
| `GET /images/json`, `GET /volumes`, `GET /networks` | 403 |
| `POST /volumes/create`, `POST /networks/create` | 403 |
| `GET /containers/<id>/archive`, `GET /containers/<id>/export` | 403 |

End to end: after `docker compose --profile demo up -d --build`, the backend was healthy, a login through the dashboard on port 3000 returned 204, and `GET /api/services` listed the local engine's containers in three service groups, with none of Bleep's own containers.

## What it shows

- **The allowlist has to be written, not switched on.** The proxy's flags are per area of the API, and `/containers` holds both the harmless reads and the dangerous writes. A written rule per path is the only way to get "restart but not create".
- **What a compromised backend can still do:** read every container's configuration and logs on the machine (environment variables included), and start, stop or restart any container. It cannot start a new one, run a command in one, delete one, or reach images, volumes or networks.
- **The proxy has no network route out.** It sits on an internal network shared only with the backend and publishes no port.

## Limits

- Restart, stop and start of any container are allowed, including the proxy's and Bleep's own. Bleep's own tools decide what the agent may act on; the proxy does not.
- Reading logs and container configuration can expose secrets that are in a container's environment. Bleep redacts them before a model sees them, but the proxy does not.
- The socket is mounted read-only into the proxy, which only stops the proxy from replacing the file; it does not limit the API calls it forwards. The proxy itself runs as root with the socket, so it is the one component that must be trusted.
- Measured on Docker Desktop for macOS only. On Linux the same template applies, but the socket's group and path are the machine's.
- The rules match paths. A new Docker API area under `/containers` that writes would be refused until the file allows it, and a new read would be allowed.

## Reproduce

```bash
cp .env.example .env   # set BLEEP_ADMIN_PASSWORD (12+ characters) and BLEEP_SECRET
docker compose --profile demo up -d --build
docker run -d --name bleep-proxy-test alpine sleep 300
docker compose exec backend python - <<'PY'
import urllib.request, urllib.error
for method, path in [("POST", "/containers/create"), ("POST", "/containers/bleep-proxy-test/restart"),
                     ("POST", "/containers/bleep-proxy-test/exec")]:
    request = urllib.request.Request("http://socket-proxy:2375" + path, method=method)
    try:
        print(method, path, urllib.request.urlopen(request).status)
    except urllib.error.HTTPError as error:
        print(method, path, error.code)
PY
docker rm -f bleep-proxy-test
docker compose --profile demo down
```

Expect 403, 204, 403.
