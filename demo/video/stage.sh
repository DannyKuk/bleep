#!/usr/bin/env bash
# The throwaway Bleep for the demo video (docs/demo.md "The README video").
#   stage.sh up      write .local/ (hosts file, env with a fresh password), start the estate
#                    (demo/video/estate/) and the throwaway Bleep
#   stage.sh down    stop both and delete the throwaway's volume
#   stage.sh reset   a fresh database for another take, keeping the model key
#   stage.sh seed    write the staged history (demo/video/seed.py) into the throwaway
#   stage.sh phone   the filmed incident's chat messages (demo/video/phone.py) to .local/run/
#   stage.sh password  print the dashboard password of this throwaway instance
set -euo pipefail
cd "$(dirname "$0")/../.."
LOCAL=demo/video/.local
COMPOSE=(docker compose -p bleep-video -f docker-compose.yaml -f demo/video/compose.video.yaml --profile demo)

write_local() {
  mkdir -p "$LOCAL"
  sed -e 's/^  - id: demo$/  - id: web-01/' \
      -e 's/^    name: Demo stack$/    name: web-01/' \
      -e 's#http://host.docker.internal:8080#http://web-01:8080#' \
      config/hosts.demo.yaml > "$LOCAL/hosts.video.yaml"
  cat >> "$LOCAL/hosts.video.yaml" <<'YAML'
      edge-proxy:
        urls:
          - url: http://web-01:8081/
            body_contains: ok
      docs-site:
        urls:
          - url: http://web-01:8082/
YAML
  if [ ! -f "$LOCAL/video.env" ]; then
    umask 077
    {
      echo "BLEEP_ADMIN_PASSWORD=$(openssl rand -hex 16)"
      echo "BLEEP_SECRET=$(openssl rand -hex 32)"
    } > "$LOCAL/video.env"
  fi
}

case "${1:-}" in
  up)
    write_local
    for f in demo/video/estate/*.yaml; do docker compose -f "$f" up -d; done
    "${COMPOSE[@]}" up -d
    ;;
  down)
    "${COMPOSE[@]}" down -v
    for f in demo/video/estate/*.yaml; do docker compose -f "$f" down; done
    ;;
  reset)
    # A new database for a fresh take, keeping secrets.json (the model key typed on Settings).
    "${COMPOSE[@]}" stop backend frontend
    docker run --rm -v bleep-video_bleep-data:/data alpine:3 \
      sh -c 'rm -f /data/bleep.db* /data/checkpoints.sqlite* /data/bleep.sqlite*'
    "${COMPOSE[@]}" up -d
    ;;
  seed) docker exec -i bleep-video-backend-1 python - < demo/video/seed.py ;;
  phone)
    docker run --rm -i -v bleep-video_bleep-data:/data:ro bleep-backend:latest \
      python -c "$(cat demo/video/phone.py)" \
      < "$LOCAL/run/incident.json" > "$LOCAL/run/phone.json"
    ;;
  password) sed -n 's/^BLEEP_ADMIN_PASSWORD=//p' "$LOCAL/video.env" ;;
  *) echo "usage: $0 up|down|reset|seed|phone|password" >&2; exit 2 ;;
esac
