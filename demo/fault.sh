#!/usr/bin/env bash
# Switch a fault of the demo shop on or off, one at a time:
#
#   demo/fault.sh <fault> on|off
#   demo/fault.sh all off
#
# Faults: bad-env oom db-down bad-deploy disk-full port-conflict misroute hung.
# docs/demo.md says what each one breaks and what Bleep should find and propose.
# It needs docker with the compose plugin, and acts on the engine docker points at: this
# machine's, or another's through DOCKER_HOST (docs/demo.md, "On another host").
set -euo pipefail

FAULTS="bad-env oom db-down bad-deploy disk-full port-conflict misroute hung"
FILL_FILE=/uploads/catalog-export.bin
SHOP_COMPOSE=$(cd "$(dirname "$0")" && pwd)/compose.yaml

usage() {
  echo "usage: $0 <fault> on|off   (faults: $FAULTS)" >&2
  echo "       $0 all off" >&2
  exit 2
}

# docker compose on the shop's own project, `shop`.
compose() { docker compose -p shop -f "$SHOP_COMPOSE" "$@"; }

# Recreate a service from its Compose definition; the caller's environment picks the variant.
recreate() { compose up -d --no-deps "$1"; }

# Load a Caddyfile into the running shop-proxy, waiting a few seconds for one that just started.
caddy_load() {
  local try
  for try in 1 2 3 4 5 6 7 8 9 10; do
    docker exec shop-proxy caddy reload --config "/etc/caddy/$1" --adapter caddyfile 2>/dev/null \
      && return 0
    sleep 1
  done
  echo "shop-proxy did not take /etc/caddy/$1" >&2
  return 1
}

bad_env_on() { SHOP_DATABASE_URL=postgresql://shop:shop@db:5432/shop recreate shop-web; }
bad_env_off() { recreate shop-web; }

oom_on() { WORKER_BATCH_MB=256 recreate shop-worker; }
oom_off() { recreate shop-worker; }

db_down_on() { docker stop shop-db; }
db_down_off() { docker start shop-db; }

bad_deploy_on() { SHOP_VERSION=2 recreate shop-web; }
bad_deploy_off() { recreate shop-web; }

disk_full_on() {
  # Writes until the 20 MB uploads volume is full. The write is meant to fail, with
  # "No space left on device"; any other failure is a real one.
  local err
  if err=$(docker exec shop-web sh -c "cat /dev/zero > $FILL_FILE" 2>&1); then
    echo "the uploads volume did not fill" >&2
    return 1
  fi
  case "$err" in
    *"No space left on device"*) ;;
    *)
      echo "$err" >&2
      return 1
      ;;
  esac
}
disk_full_off() { docker exec shop-web rm -f "$FILL_FILE"; }

port_conflict_on() {
  local image
  image=$(docker inspect -f '{{.Config.Image}}' shop-proxy)
  docker stop shop-proxy
  docker run -d --name shop-canary -p 8080:8080 "$image" caddy respond --listen :8080 "shop canary"
  # Fails: the canary holds port 8080 now.
  docker start shop-proxy || true
}
port_conflict_off() {
  docker rm -f shop-canary
  # Recreated, not started: after a start that failed on the port, Docker 29 starts the same
  # container without publishing the port again.
  compose up -d --no-deps --force-recreate shop-proxy
}

misroute_on() { caddy_load Caddyfile.misroute; }
misroute_off() { caddy_load Caddyfile; }

hung_on() {
  # /freeze blocks shop-web's event loop; the call itself never answers, so it times out here.
  docker exec shop-web python -c "import urllib.request as u
try:
    u.urlopen(u.Request('http://localhost:8000/freeze', method='POST'), timeout=2)
except Exception:
    pass"
}
hung_off() { docker restart shop-web; }

all_off() {
  docker rm -f shop-canary 2>/dev/null || true
  docker start shop-db
  docker exec shop-web rm -f "$FILL_FILE" 2>/dev/null || true
  # Recreating puts shop-web and shop-worker back on their defaults, ends a freeze and gives
  # shop-proxy its port back.
  compose up -d --no-deps --force-recreate shop-web shop-worker shop-proxy
  caddy_load Caddyfile
}

[ $# -eq 2 ] || usage
fault=$1 state=$2
case "$state" in on | off) ;; *) usage ;; esac

if [ "$fault" = all ]; then
  [ "$state" = off ] || usage
  all_off
  exit 0
fi
case " $FAULTS " in *" $fault "*) ;; *) usage ;; esac

"${fault//-/_}_$state"
echo "$fault $state"
