-- One Telegram thread per incident (#380). An incident's short name ("shop #14"): its subject's name
-- when it opened and a running number per subject. Older incidents keep NULL and are named by id.
ALTER TABLE incidents ADD COLUMN short_name TEXT;
-- Every message about an incident is logged now, so a reply to any of them finds it; only those
-- with buttons are cleared when the incident ends. Every older row was logged for its buttons.
ALTER TABLE telegram_messages ADD COLUMN buttons INTEGER NOT NULL DEFAULT 1;
