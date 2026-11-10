# Kramerladen

Kleiner Webshop für einen Laden in Leipzig. Besteht aus `shop-web` (die Flask-App), `shop-db` (Postgres 16) und einem Reverse Proxy davor. Läuft als Compose-Projekt `shop` auf dem Server `hetzner-1`.

## Starten und Stoppen

Im Ordner `/srv/shop` mit `docker compose up -d` starten. Zum Stoppen `docker compose stop`, niemals `docker compose down -v`, sonst ist das Datenbank-Volume `shop_pgdata` weg und damit alle Bestellungen. Nach einem Neustart des Servers kommen die Container von selbst wieder hoch, weil die restart policy `unless-stopped` ist.

## Konfiguration

Alle Einstellungen stehen in `.env`. Wichtig sind `DATABASE_URL`, `SECRET_KEY` und `MAIL_HOST`. Fehlt `SECRET_KEY`, startet `shop-web` zwar, aber alle Sessions werden bei jedem Restart ungültig und Kunden werden aus dem Warenkorb geworfen. Zahlungs-Keys (Stripe) kommen aus `STRIPE_KEY`, nie ins Git einchecken.

## Healthcheck

`shop-web` hat einen Healthcheck auf `/healthz`, alle 30 Sekunden, Timeout 5 Sekunden, nach 3 Fehlversuchen gilt der Container als unhealthy. `/healthz` prüft auch die Verbindung zur Datenbank. Ist `shop-db` weg oder voll, wird also `shop-web` ebenfalls unhealthy, obwohl die App selbst läuft. Erst die DB anschauen!

## Datenbank-Migrationen

Migrationen laufen mit `docker compose run --rm shop-web flask db upgrade`. Vor jedem Deploy ein Dump: `pg_dump` in den Ordner `/srv/backups/shop`. Wenn eine Migration mittendrin abbricht, steht die Tabelle `alembic_version` auf dem alten Stand, dann Migration einfach nochmal starten.

## Bekannte Probleme

- Bilder-Upload bricht bei Dateien über 8 MB ab (Limit im Proxy, `client_max_body_size`).
- Nach dem Deploy zeigt die Startseite manchmal noch die alte Version, wegen dem Cache im Proxy. Cache leeren oder 5 Minuten warten.
- Der Mailversand ist langsam, wenn `MAIL_HOST` nicht erreichbar ist: jede Bestellung hängt dann bis zu 30 Sekunden.
