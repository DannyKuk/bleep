-- Execute's crash-safety ledger: one row per step of an approved plan, written `started` before
-- the engine call and `done` or `failed` after it, or `skipped` when an earlier step failed. A
-- resumed run reads it, so a finished step is never run again (Review Focus 1).
CREATE TABLE action_log (
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    plan_hash TEXT NOT NULL,
    step_index INTEGER NOT NULL,      -- 0-based place in the plan
    action TEXT NOT NULL,
    host_id TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'done', 'failed', 'skipped')),
    detail TEXT NOT NULL DEFAULT '',
    started_at TEXT,                  -- NULL for a skipped step
    finished_at TEXT,                 -- NULL until done or failed, and for a skipped step
    PRIMARY KEY (incident_id, plan_hash, step_index)
);
