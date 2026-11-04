-- The dev panel's prompt overrides in force when the incident's run started (#130), as JSON: each
-- prompt name with a hash of its override, never the text. NULL for a run not started yet or
-- started before this column; `{}` when none was in force.
ALTER TABLE incidents ADD COLUMN prompt_overrides TEXT;
