-- One row per plan put to the human. A decision is a single UPDATE ... WHERE status = 'pending',
-- so the first channel to answer wins and every later one changes nothing.
CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    plan_hash TEXT NOT NULL,
    proposal TEXT NOT NULL,           -- Proposal JSON
    snapshots TEXT NOT NULL,          -- [TargetSnapshot] JSON, one per target container
    status TEXT NOT NULL,             -- pending, approved, rejected, instructed
    requested_at TEXT NOT NULL,
    decision TEXT,                    -- Decision JSON, set with the status
    reminders INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX approvals_one_pending_per_incident ON approvals (incident_id)
    WHERE status = 'pending';
CREATE INDEX approvals_incident ON approvals (incident_id);
