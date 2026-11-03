-- The link to the incident's trace when tracing is on (#40). A link only: the tracing keys stay in
-- the environment.
ALTER TABLE incidents ADD COLUMN trace_url TEXT;
