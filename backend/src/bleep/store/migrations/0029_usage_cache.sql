-- Prompt caching on the Anthropic provider (#529): of a call's `tokens_in`, how many were read from
-- the cache and how many were written to it. Both are parts of `tokens_in`, which still counts every
-- input token, so the sums over `tokens_in` and the incident token cap are as before. Rows from
-- before, and from providers that report no cache, keep 0.
ALTER TABLE usage ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usage ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0;
