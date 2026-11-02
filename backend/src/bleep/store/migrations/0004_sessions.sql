-- Admin sessions. token_hash is the sha256 of the cookie token, never the token itself.
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX sessions_expires ON sessions (expires_at);
