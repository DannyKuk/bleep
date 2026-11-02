-- One row per model call. usd is NULL when the cost of a cloud model is unknown.
CREATE TABLE usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    usd REAL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX usage_incident ON usage (incident_id);
CREATE INDEX usage_day ON usage (day);
