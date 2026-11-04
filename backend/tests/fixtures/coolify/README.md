# Coolify fixtures

Shaped after a real Coolify v4.6.0 server on 2026-11-04 (`GET /api/v1/applications` and `GET /api/v1/deployments/applications/{uuid}?take=2`), then written by hand: only the shapes were kept, and every id, uuid, name, domain, commit, message and value is made up. They are trimmed to the fields Bleep reads plus a few secret-bearing ones (webhook secrets, a basic-auth password, build logs) that the tests prove never reach a tool's output. See docs/design/plugins.md.

| File | Request |
|---|---|
| `applications.json` | `GET /api/v1/applications` |
| `deployments.json` | `GET /api/v1/deployments/applications/{uuid}` (`{count, deployments}`, as the server answers, not the list the spec documents) |

Not read on the real server, so shaped after the v4.6.0 OpenAPI spec and the controller code instead: `envs.json` (`GET /api/v1/applications/{uuid}/envs`; no env values were fetched), `deploy.json` (`POST /api/v1/deploy`; nothing was deployed) and `unauthenticated.json` (the spec's 401 body).
