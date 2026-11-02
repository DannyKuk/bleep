-- Long-term memory (#29): one record per finished incident, with the vector of its text, and
-- facts per service. `model` names the embedder the vector came from; on boot every record with
-- another name is embedded again, and until then it is left out of recall.
CREATE TABLE memory_incidents (
    incident_id TEXT PRIMARY KEY REFERENCES incidents (id),
    host_id TEXT NOT NULL,
    service_id TEXT,                  -- NULL for a host incident
    date TEXT NOT NULL,               -- when the incident opened
    title TEXT NOT NULL,
    cause TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    human_decision TEXT,
    feedback TEXT,
    text TEXT NOT NULL,               -- what was embedded
    model TEXT NOT NULL,
    embedding BLOB NOT NULL           -- float32, unit length
);
CREATE INDEX memory_incidents_model ON memory_incidents (model);

CREATE TABLE service_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('report', 'human')),
    created_at TEXT NOT NULL
);
CREATE INDEX service_facts_service ON service_facts (service_id);
