-- What the sessions were started under (#619): an HMAC-SHA256 of the admin password keyed with
-- BLEEP_SECRET, never the password. When it differs at startup, every session ends.
CREATE TABLE session_password (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_mac TEXT NOT NULL
);
