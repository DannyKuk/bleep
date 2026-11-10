# Runbook: Reverse Proxy (nginx)

Ein nginx-Container `edge-proxy` auf hetzner-1 nimmt Port 80 und 443 an und leitet weiter: `/` an `shop-web:8000`, `cloud.example.org` an `cloud-app:80`.

## TLS Zertifikate

Zertifikate kommen von Let's Encrypt, geholt von `certbot` in einem eigenen Container, renewal per Cron jeden Tag 03:15. Die Certs liegen im Volume `edge_certs`. Wenn die Seite "Zertifikat abgelaufen" / ERR_CERT_DATE_INVALID zeigt: `docker logs certbot` ansehen, meistens ist Port 80 blockiert (die ACME challenge geht über http) oder das Rate Limit von LE ist erreicht. Nach manuellem Renewal nginx reloaden.

## 502 Bad Gateway

502 heisst: nginx lebt, aber der upstream antwortet nicht. Erst `docker ps` ob `shop-web` laeuft und healthy ist, dann ob der Port im `proxy_pass` noch stimmt. Nach dem Deploy von v2.3 war der Port von 8000 auf 8080 geaendert und die proxy conf nicht, das gab 502 für alle.

## Config neu laden

Nach Aenderungen an `nginx.conf` erst `docker exec edge-proxy nginx -t` (syntax test), und nur wenn ok `nginx -s reload`. Ein reload unterbricht keine laufenden Verbindungen, ein restart schon. Bei Syntaxfehler startet der Container nach restart nicht mehr hoch (exit 1)!

## Upload-Limit

`client_max_body_size` steht global auf 8m. Fuer die Cloud ist es auf 10g erhoeht (location-Block). Wer 413 Request Entity Too Large sieht, hat das Limit gerissen.

## Rate limiting

`limit_req_zone` 10 Requests pro Sekunde pro IP, burst 20. Bei Angriffen (viele 429) kann man die IP in `blocklist.conf` eintragen. Logs: `/var/log/nginx/access.log`, wird von logrotate woechentlich rotiert, 8 Wochen behalten.
