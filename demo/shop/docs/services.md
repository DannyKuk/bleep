# The shop's services

The shop is a small web shop: a list of items in a database, served as JSON behind a proxy. It
runs as one Compose project, `shop` ([`demo/compose.yaml`](../../compose.yaml)), with four
containers and two volumes.

| Container | Image | What it does | Port |
|---|---|---|---|
| `shop-proxy` | Caddy, built from `demo/proxy` | The public entry: forwards requests to `shop-web`. | 8080, published on the host |
| `shop-web` | Python 3.14 with FastAPI, built from `demo/shop` | The shop's API. | 8000, inside the network only |
| `shop-db` | `postgres:17-alpine` | Holds the `items` table. | 5432, inside the network only |
| `shop-worker` | Python 3.14, built from `demo/worker` | Processes the uploads in batches. | none |

Requests go `shop-proxy` → `shop-web` → `shop-db`. Only port 8080 is reachable from outside the
Compose network; the containers reach each other by container name.

## Endpoints

Through `http://<host>:8080`:

- `GET /health` answers `{"status": "ok", "version": "<version>"}` when shop-web can query the
  database and write to the uploads volume, and 503 with the part that failed otherwise.
- `GET /api/items` lists the items, each with its id, name and price.

## Data

- **`items`** in shop-db: id, name, price. shop-web creates the table on its first start and
  seeds three items. The data lives in the `shop-db` volume, so it outlives the container.
- **`uploads`**: a 20 MB in-memory (tmpfs) volume. shop-web writes to it and shop-worker reads
  it. Its files last while a container that mounts it runs.

## Dependencies

shop-web connects to shop-db at startup and on every request; Compose starts it only once
shop-db's health check passes. shop-worker needs only the uploads volume. shop-proxy needs
shop-web to answer on port 8000.

## Health checks

- shop-web: `GET http://localhost:8000/health` every 10 s, timeout 5 s, 3 retries, 10 s start
  period.
- shop-db: `pg_isready` every 5 s, timeout 3 s, 5 retries.

shop-proxy and shop-worker have none.
