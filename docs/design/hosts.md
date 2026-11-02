# Hosts

How Bleep is told which Docker hosts to watch.

- **Secrets by reference only.** A token is `{env: NAME}` or `{file: PATH}`, and a literal string where a reference is expected is rejected naming the field. Reason: secrets never live in the database, in YAML that gets committed, or anywhere the model can read them (#3).
- **`extra="forbid"` everywhere.** An unknown key is an error at startup. Reason: a typo such as `known_host_file` would otherwise silently drop a safety setting (#3).
- **Two host kinds.** `ssh` (address, user, key file) for real servers and `socket` (a `unix://` or `tcp://` endpoint) for the demo stack and the machine Bleep runs on. Reason: the combination is validated so a half-filled host fails early (#3).
- **Key and known_hosts are mounted file paths.** The YAML holds the path, never the key. Reason: the key stays a mounted secret and the file is safe to share (#3).
- **Numbered SQL migrations in the package.** `store/migrations/NNNN_*.sql` run once each, recorded in `schema_version`. Reason: later tasks add their own tables without a migration framework (#3).
