-- An incident's tokens and cost are the usage ledger's sums (IncidentRepo._COLUMNS); these columns
-- were never written or read.
ALTER TABLE incidents DROP COLUMN tokens;
ALTER TABLE incidents DROP COLUMN cost_usd;
