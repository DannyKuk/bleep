-- The dev panel's prompt overrides (#42): text that stands in for a prompt file. Never a secret:
-- the store refuses secret-shaped text, on save and again on load.
CREATE TABLE prompt_overrides (
    name TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
