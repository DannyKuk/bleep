-- Hosts added on the dashboard (#194): how Bleep reaches each one. Its SSH key and the host key a
-- person confirmed live in the secret file (secrets.json), never here.
CREATE TABLE dashboard_hosts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    port INTEGER NOT NULL,
    user TEXT NOT NULL,
    docker_socket TEXT NOT NULL,
    revision INTEGER NOT NULL,        -- bumped by every change, a key's included: the version
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
