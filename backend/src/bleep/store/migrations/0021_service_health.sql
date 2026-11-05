-- Each service's health as the watcher last judged it, and since when it has had it (#232).
-- `unknown` is never stored: no data says nothing, so the last known health and its time stay.
CREATE TABLE service_health (
    service_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    health TEXT NOT NULL CHECK (health IN ('ok', 'degraded', 'down', 'stopped')),
    since TEXT NOT NULL
);
CREATE INDEX service_health_host ON service_health (host_id);
