-- Folded repeats (#258): an incident that repeats a finished one of the same kind on the same
-- subject within the fold window points at the group's first incident. Older rows stay NULL and
-- never fold.
ALTER TABLE incidents ADD COLUMN fold_key TEXT;
ALTER TABLE incidents ADD COLUMN fold_of TEXT REFERENCES incidents (id);
CREATE INDEX incidents_fold ON incidents (fold_key, opened_at);
CREATE INDEX incidents_fold_of ON incidents (fold_of);
