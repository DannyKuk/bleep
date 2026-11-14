-- The outcome of an incident that closed outside its run with one of its own: `recovered`, its
-- service healthy again on its own while its plan waited for a yes (#571). A run keeps its
-- outcome in its checkpoint as before; every other incident keeps NULL.
ALTER TABLE incidents ADD COLUMN outcome TEXT;
