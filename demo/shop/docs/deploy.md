# Deploying the shop

The shop is deployed with Docker Compose from [`demo/compose.yaml`](../../compose.yaml). That
file is the only place its settings live: change them there and apply them with Compose, never by
hand on the host.

```bash
docker compose -f demo/compose.yaml up -d --build    # build and start, or apply a change
docker compose -f demo/compose.yaml ps               # what runs
docker compose -f demo/compose.yaml logs shop-web    # one container's logs
docker compose -f demo/compose.yaml down             # stop; add -v to drop the volumes
```

`up -d` recreates only the containers whose settings changed.

## Settings

| Variable | Container | Default | What it sets |
|---|---|---|---|
| `DATABASE_URL` | shop-web | the `shop` database on `shop-db:5432` | Where shop-web finds its database. |
| `SHOP_VERSION` | shop-web | `1` | The release shop-web runs and reports at `/health`. |
| `DEMO_INJECT` | shop-web | `0` | A test hook; stays `0`. |
| `SHOP_API_KEY` | shop-web | a placeholder | Reserved; shop-web does not use it. |
| `WORKER_BATCH_MB` | shop-worker | `8` | How many MB shop-worker holds in memory per batch. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | shop-db | set in the Compose file | The database and its user. |

## Limits and restarts

- Every container restarts `unless-stopped`, and each has a fixed `container_name`
  (`shop-web`, not `shop-shop-web-1`).
- shop-worker is limited to 64 MB of memory with no swap.
- The uploads volume is capped at 20 MB.
- shop-proxy's Caddyfile is built into its image ([`demo/proxy`](../../proxy)).
