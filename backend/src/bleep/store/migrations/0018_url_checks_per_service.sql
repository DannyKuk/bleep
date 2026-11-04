-- Two services on one host may check the same URL, so a result belongs to (host, service, url).
CREATE TABLE url_checks_new (
    host_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    url TEXT NOT NULL,
    ok INTEGER NOT NULL,
    status INTEGER,
    latency_ms INTEGER NOT NULL,
    error TEXT,
    body_matched INTEGER,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (host_id, service_id, url)
);
INSERT INTO url_checks_new (host_id, service_id, url, ok, status, latency_ms, error, body_matched, checked_at)
    SELECT host_id, service_id, url, ok, status, latency_ms, error, body_matched, checked_at FROM url_checks;
DROP TABLE url_checks;
ALTER TABLE url_checks_new RENAME TO url_checks;
