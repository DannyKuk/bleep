# nightly backup

restic backup job, laeuft jede Nacht 02:00 als Container `backup` (cron im container, image `mazzolino/restic`).

## Was wird gesichert

- Postgres dump von shop-db (`pg_dump`, vorher als file)
- Volume `cloud_data` (Nextcloud files)
- `/srv/*/.env`

Ziel: S3-bucket bei Hetzner Object Storage (`s3:fsn1.your-objectstorage.com/kramer-backups`), restic repo password in `RESTIC_PASSWORD`, **nicht verlieren**, ohne das sind alle Backups wertlos.

## Retention

keep-daily 7, keep-weekly 4, keep-monthly 6. `restic forget --prune` läuft sonntags. prune dauert ~20min und braucht viel RAM.

## Restore

`restic snapshots` zeigt die Liste, dann `restic restore latest --target /tmp/restore --include /data/shop`. Datenbank: dump zurückspielen mit `psql < shop.sql`. Mind. 1x im Quartal testen!!! (letzter test: nie)

## Fehler: repository is already locked

Passiert wenn ein Job abgebrochen wurde (Server-Reboot waehrend backup). Fix: `restic unlock`. Nur wenn sicher kein anderer restic prozess läuft, sonst `restic list locks` erst ansehen.

## Monitoring

Wenn der Job fehlschlägt, schickt er eine Mail (`BACKUP_MAIL_TO`). Kommt keine Mail weil der Mailserver down ist, merkt es niemand -> noch offen: healthchecks.io ping.
