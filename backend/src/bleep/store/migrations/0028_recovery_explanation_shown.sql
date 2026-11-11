-- A recovery line Bleep wrote in place of the cheap model's (no logs, the cap spent, a held crash
-- loop) keeps the fixed text it was made from, as JSON, to show it in the person's language
-- (#455). `explanation` stays the English every reader got before. The model's own lines, and
-- every older row, keep NULL and show as stored.
ALTER TABLE recovery_events ADD COLUMN explanation_shown TEXT;
