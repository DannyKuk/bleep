-- Services and hosts a person snoozed or left alone (#220): Bleep opens no incident for them.
CREATE TABLE quiet (
    scope TEXT NOT NULL,
    target_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    until TEXT,
    since TEXT NOT NULL,
    PRIMARY KEY (scope, target_id)
);
