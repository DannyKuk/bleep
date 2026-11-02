-- subject_key is "kind:host_id:service_id"; the partial unique index allows one open incident each.
CREATE TABLE incidents (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    host_id TEXT NOT NULL,
    service_id TEXT,
    subject_key TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    cost_usd REAL,
    tokens INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX incidents_one_open_per_subject ON incidents (subject_key)
    WHERE status NOT IN ('resolved', 'closed', 'failed');
CREATE INDEX incidents_status ON incidents (status, opened_at);

CREATE TABLE incident_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    signal TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX incident_signals_incident ON incident_signals (incident_id);

CREATE TABLE incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    at TEXT NOT NULL,
    UNIQUE (incident_id, seq)
);

-- A container that died and came back inside the grace window: logged, not an incident.
CREATE TABLE recovery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id TEXT NOT NULL,
    service_id TEXT,
    container TEXT NOT NULL,
    at TEXT NOT NULL,
    explanation TEXT NOT NULL
);
CREATE INDEX recovery_events_at ON recovery_events (at);
