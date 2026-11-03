-- Human feedback (#30): one vote per incident and finding, a later vote replacing the earlier one.
-- Report facts name the incident they were learned from, so a re-run Report replaces its own.
CREATE TABLE incident_feedback (
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    target TEXT NOT NULL CHECK (target IN ('diagnosis', 'proposal')),
    vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
    note TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (incident_id, target)
);

ALTER TABLE service_facts ADD COLUMN incident_id TEXT;  -- NULL for a fact a person added
CREATE INDEX service_facts_incident ON service_facts (incident_id);
