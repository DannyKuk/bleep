# shop-worker

Background worker fuer den Shop. Holt jobs aus der Redis queue `shop:jobs` und macht: Bestell-Mails, Rechnungs-PDFs, Lagerbestand sync. Image `registry.local/shop-worker`, ein Container pro Server.

## Env vars

- `REDIS_URL` - muss gesetzt sein! ohne wird der worker sofort mit exit code 1 beendet, steht dann im log "REDIS_URL missing"
- `WORKER_CONCURRENCY` - default 4
- `JOB_TIMEOUT` - sekunden, default 120

Nach einem Deploy bitte immer checken ob die env noch da ist, hatten wir schon 2x.

## Retries und dead letter

Ein job der failt wird 3 mal wiederholt (backoff 10s, 60s, 5min). Danach landet er in der Liste `shop:dead`. Dead jobs werden NICHT automatisch gelöscht. Anschauen mit `redis-cli LRANGE shop:dead 0 10`, requeue mit `python -m worker requeue-dead`.

## Memory

Der Rechnungs-PDF job (weasyprint) hat ein leak, nach ca 2000 PDFs sind es ueber 1 GB RAM. Container hat ein memory limit von 768m, dann kommt OOM kill und docker startet ihn neu (restart: always). Kein Drama, aber jobs die gerade liefen werden wiederholt. Noch offen: worker nach 500 jobs selber recyclen.

## Healthcheck

Kein HTTP. Der worker schreibt alle 15s einen heartbeat key `shop:worker:heartbeat` in Redis, healthcheck prueft ob der juenger als 60s ist. Wenn Redis down ist wird der worker also auch unhealthy.

## Graceful shutdown

SIGTERM -> worker macht den aktuellen job fertig (max `JOB_TIMEOUT`) und beendet sich dann. `stop_grace_period` in compose steht auf 130s, sonst killt docker ihn mit SIGKILL mitten im Job.
