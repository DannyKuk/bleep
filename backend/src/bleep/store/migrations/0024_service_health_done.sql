-- A one-shot job that exited 0 is `done` (#256); SQLite cannot change a CHECK, so the table is
-- rebuilt with it (#270). `unknown` and `asleep` say nothing about the service and are never stored.
CREATE TABLE service_health_new (
    service_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    health TEXT NOT NULL CHECK (health IN ('ok', 'degraded', 'down', 'stopped', 'done')),
    since TEXT NOT NULL
);
INSERT INTO service_health_new (service_id, host_id, health, since)
    SELECT service_id, host_id, health, since FROM service_health;
DROP TABLE service_health;
ALTER TABLE service_health_new RENAME TO service_health;
CREATE INDEX service_health_host ON service_health (host_id);
