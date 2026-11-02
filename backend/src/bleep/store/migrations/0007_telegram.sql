-- Which incident (and which plan) a Telegram message is about, so a reply to it becomes an
-- instruction for that incident. A single chat is answered, so message_id is unique on its own.
CREATE TABLE telegram_messages (
    message_id INTEGER PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents (id),
    approval_id TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE INDEX telegram_messages_incident ON telegram_messages (incident_id);
