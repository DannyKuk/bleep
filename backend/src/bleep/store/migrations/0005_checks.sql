-- The latest outside-in URL check per (host, url), so the dashboard can show what the last check saw.
CREATE TABLE url_checks (
    host_id TEXT NOT NULL,
    url TEXT NOT NULL,
    service_id TEXT NOT NULL,
    ok INTEGER NOT NULL,
    status INTEGER,
    latency_ms INTEGER NOT NULL,
    error TEXT,
    body_matched INTEGER,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (host_id, url)
);
