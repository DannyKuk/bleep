-- The recent URL checks per (host, service, url), for the service page's strip (#232). Pruned to
-- the last 30 rows per URL on insert; the error text is not kept, the latest row has it.
CREATE TABLE url_check_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    url TEXT NOT NULL,
    ok INTEGER NOT NULL,
    status INTEGER,
    latency_ms INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE INDEX url_check_history_url ON url_check_history (host_id, service_id, url, id);
