# Setup

## Quick start (demo)

The demo profile lets Bleep watch the Docker engine of the machine it runs on, through a filtered socket proxy ([why](decisions/socket-host.md)).

```bash
cp .env.example .env    # set BLEEP_ADMIN_PASSWORD (12+ characters) and BLEEP_SECRET (a long random string)
docker compose --profile demo up -d --build
```

Open http://localhost:3000 and log in with the admin password. Bleep refuses to start with a shorter password. A random secret: `openssl rand -hex 32`.

Without the `demo` profile only the backend and the dashboard start, and the hosts file has to name a host you reach some other way.

The data (the SQLite database) lives in the `bleep-data` volume. `docker compose down` keeps it; `docker compose down -v` deletes it.

## Real hosts

1. Copy the example and edit it: `cp config/hosts.example.yaml config/hosts.yaml`. The file is gitignored. Every field is documented in the example.
2. Put the SSH private key and a `known_hosts` file in `./secrets` (also gitignored), and point the hosts file at them as `/run/secrets/...`:
   ```bash
   mkdir -p secrets/ssh
   cp ~/.ssh/bleep_deploy_key secrets/ssh/id_ed25519
   ssh-keyscan -H your-host >> secrets/ssh/known_hosts
   ```
   Keep the key at mode 600, and make the backend's user able to read it. The backend runs as uid and gid 10001, fixed in its image.
   - **Linux:** `sudo chown 10001:10001 secrets/ssh/* && chmod 600 secrets/ssh/*`. Without the chown, a 600 key owned by your own user is unreadable inside the container.
   - **Docker Desktop on macOS:** the bind mount maps ownership to the container's user, so `chmod 600 secrets/ssh/*` is enough.
   `./secrets` is mounted read-only at `/run/secrets`. Check the fingerprint `ssh-keyscan` returned against one you trust before relying on it.
3. In `.env`, set `BLEEP_HOSTS_FILE=/config/hosts.yaml`. When it is unset, compose uses `/config/hosts.demo.yaml`.
4. `docker compose up -d --build` (without the `demo` profile if you don't want the local engine watched).

Secrets inside the hosts file are references, `{env: NAME}` or `{file: PATH}`, never values. An environment variable reaches the backend from `.env`.

### What Bleep requires of an SSH host

- **Host keys are always verified, and it fails closed.** Bleep checks the `known_hosts_file` of the host if set, else `~/.ssh/known_hosts` inside the container. With neither, or with a key that doesn't match, it does not connect. There is no setting that turns checking off ([design](design/hosts.md)).
- **Encrypted private keys are not supported**, since there is no ssh-agent in the container. Use a dedicated, unencrypted deploy key for Bleep, give it to Bleep only, and revoke it by removing it from `authorized_keys`.
- **The SSH user needs access to the Docker socket, which is root-equivalent.** On Linux that means the user is in the `docker` group, or is root. Anyone who can use that key can take over the host. Treat the key like a root password: a dedicated key, a dashboard on a trusted network only, and nothing else sharing the secrets directory.
- **Docker Desktop on macOS** keeps its socket in the user's home, so the host needs `docker_socket: /Users/<user>/.docker/run/docker.sock`. The remote login also has to be on (System Settings, Sharing, Remote Login). How the socket is forwarded: [Docker over SSH](decisions/docker-over-ssh.md).

## Network and cookies

The dashboard (port 3000) is for a trusted LAN only. It has one admin login and no rate-limited public hardening. The backend's port 8000 is published on `127.0.0.1` only, so other machines reach Bleep through the dashboard, which forwards `/api` to the backend.

The watcher checks every host every 15 s; `BLEEP_WATCH_INTERVAL_S` in `.env` changes that.

The session cookie is not `Secure` by default, because the dashboard is plain http on the LAN. If you put it behind https, set `BLEEP_COOKIE_SECURE=true` in `.env`.

## Later

- Always-on on the spare MacBook, so Bleep watches the hosts when this laptop is closed.
- A public deployment behind the Coolify proxy, with hardened auth in front of the dashboard. Until that exists, do not expose port 3000 to the internet.
