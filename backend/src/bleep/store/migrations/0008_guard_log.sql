-- What the guard refused, blocked or changed. No foreign key: the watcher records redactions
-- for a self-recovered container before (or without) any incident.
CREATE TABLE guard_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX guard_log_incident ON guard_log (incident_id, id);
