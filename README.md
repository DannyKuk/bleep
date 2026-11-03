# Bleep

An on-call agent for Docker hosts: it notices when a container breaks, works out why, proposes a fix, and runs it only after you say yes.

- Watches containers on your hosts over SSH (or a filtered local socket) and groups them into services.
- Investigates a failing service with read-only tools, and proposes a fix for you to approve.
- Runs the approved fix, then checks the service is healthy again.

## Quick start

```bash
cp .env.example .env    # set BLEEP_ADMIN_PASSWORD (12+ characters) and BLEEP_SECRET
docker compose --profile demo up -d --build
open http://localhost:3000
```

- [Setup](docs/setup.md): real hosts, SSH keys, the LAN-only dashboard.
- [Demo](docs/demo.md): the demo shop and its eight faults, with what Bleep should find and propose for each.
- [Architecture](docs/architecture.md): the pieces and one incident from start to end.
- [Decisions](docs/decisions/): why it is built the way it is.
- [Evals](docs/design/evals.md): how Bleep is scored on real faults, and [how to run them](backend/evals/README.md).
