# Incidents

How signals become incidents and how an incident's progress reaches the dashboard.

- **One open incident per subject, enforced by the database.** A partial unique index on `subject_key` (`kind:host_id:service_id`) covers every status except resolved, closed and failed. Reason: two watchers firing at once must end with one incident, and a code-level check alone loses that race (#11).
- **`open_or_attach` takes the write lock first.** It begins an immediate transaction, attaches if an open incident exists, and on a unique-index conflict re-selects and attaches. Reason: concurrent callers serialize instead of failing, and the index stays the backstop (#11).
- **A host that is unreachable absorbs its services' signals.** A service signal attaches to the host's open incident when that incident has a `host_unreachable` signal. Reason: when the host is down every service on it looks down, and one incident per service would page about one cause many times (#11).
- **A finished incident absorbs nothing.** Resolved, closed and failed incidents leave the index, so the next signal opens a fresh incident. Reason: a recurrence after a fix is a new story with its own diagnosis and report (#11).
- **Ids are `inc_` plus a ULID-like string.** Ten characters of millisecond time then eight random ones, from the injected clock. Reason: ids sort by creation without a new dependency (#11).
- **The bus persists before it fans out.** `publish` writes the event with `seq = max + 1` for its incident inside one immediate transaction, then pushes it to subscribers. Reason: a crash or a dropped subscriber never loses an event, and `seq` has no gaps or duplicates (#11).
- **Subscriber queues are bounded and drop the oldest.** Each subscriber has a queue of 256; when it is full the oldest event goes and publishing never waits. Reason: one stalled browser tab must not slow the agent, and the client fills the gap from `history(incident_id, after_seq)` (#11).
- **`subscribe(None)` receives every incident.** Reason: the overview page shows all incidents live (#11).
- **Subscribers register when iteration starts and unregister on close or cancel.** Reason: a closed SSE connection must not leave a queue filling forever (#11).
- **Clocks are injected, timestamps are UTC.** Stored as ISO strings. Reason: tests control time and ordering without sleeping (#11).
