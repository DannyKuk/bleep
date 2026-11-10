# Cloud (Nextcloud) - Betrieb

Die Firmen-Cloud unter cloud.example.org. Container `cloud-app` (Apache + PHP), Datenbank `cloud-db` (MariaDB), Redis `cloud-redis` fuer file locking. Dateien im Volume `cloud_data`.

## Cron

Die Hintergrundjobs laufen per Cron alle 5 min (`php -f cron.php` im Container). Wenn Cron nicht läuft zeigt die Admin-Seite "Letzter Job vor X Stunden" in rot, Dateien werden nicht aufgeraeumt, Benachrichtigungen kommen verspaetet. Pruefen mit `docker exec -u www-data cloud-app php occ background:job:list`.

## Wartungsmodus

Vor Updates: `occ maintenance:mode --on`, nach dem Update wieder `--off`. Bleibt der Wartungsmodus nach einem abgebrochenen Update haengen sehen alle Nutzer "Instanz im Wartungsmodus". Dann `--off` ausführen, danach `occ upgrade`.

## Vertrauenswürdige Domains

Greift jemand per neuer Domain oder IP zu kommt "Zugriff über eine nicht vertrauenswürdige Domain". Fix: die Domain in `config/config.php` bei `trusted_domains` ergaenzen. Hinter dem proxy auch `overwriteprotocol` => https setzen, sonst gibts Redirect-Schleifen bei Login.

## Uploads grosser Dateien

PHP `upload_max_filesize` und `post_max_size` stehen auf 10G, `max_execution_time` 3600. Bei Uploads > 10 GB bricht der Browser ab. Chunked upload nimmt der client eh automatisch, aber der proxy muss `proxy_request_buffering off` haben, sonst läuft /tmp im proxy voll.

## Speicherplatz

Wenn die Platte vollläuft: erst `du -sh` auf `cloud_data`, die meisten Platz fressen Papierkorb und alte Dateiversionen. `occ trashbin:cleanup --all-users` und `occ versions:cleanup`. Die Datenbank-Logs (`cloud-db`) wachsen auch, binlog expire steht auf 3 Tage.

## Redis locks

"Datei ist gesperrt" / 423 Locked heisst meistens verwaiste Locks in Redis nach einem Absturz. `docker exec cloud-redis redis-cli FLUSHDB` löscht sie (nur DB 0, die Cloud nutzt nix anderes dort). Nie bei laufenden Uploads machen.
