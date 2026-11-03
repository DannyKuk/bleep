-- The action log's plan column holds `<plan hash>:<approval id>`, not a bare hash: the same plan
-- approved twice must run twice. Renamed so the name says what it holds (#87); rows keep their
-- values, and the primary key follows the column.
ALTER TABLE action_log RENAME COLUMN plan_hash TO plan_key;
