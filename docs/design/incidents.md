# Incidents

How signals become incidents and how an incident's progress reaches the dashboard.

- **One open incident per subject, enforced by the database.** A partial unique index on `subject_key` (`kind:host_id:service_id`) covers every status except resolved, closed and failed. Reason: two watchers firing at once must end with one incident, and a code-level check alone loses that race (#11).
- **`open_or_attach` takes the write lock first.** It begins an immediate transaction, so concurrent callers serialize on the lock and the second sees the first's incident and attaches. The unique index, with one re-select and attach on conflict, is only the backstop. Reason: callers wait instead of failing (#11).
- **A host that is unreachable absorbs its services' signals.** A service signal attaches to the host's open incident when that incident has a `host_unreachable` signal. Reason: when the host is down every service on it looks down, and one incident per service would page about one cause many times (#11).
- **A finished incident absorbs nothing.** Resolved, closed and failed incidents leave the index, so the next signal opens a fresh incident. Reason: a recurrence after a fix is a new story with its own diagnosis and report (#11).
- **Ids are `inc_` plus a ULID-like string.** Ten characters of millisecond time then eight random ones, from the injected clock. Reason: ids sort by creation without a new dependency (#11).
- **The bus persists before it fans out.** `publish` writes the event with `seq = max + 1` for its incident inside one immediate transaction, then pushes it to subscribers. Reason: a crash or a dropped subscriber never loses an event, and `seq` has no gaps or duplicates (#11).
- **Persist and fan-out run under one lock.** Reason: subscribers always receive events in seq order, never seq 2 before seq 1 (#11).
- **Subscribing is a context manager that registers on enter.** The catch-up pattern is subscribe, fetch `history`, then iterate de-duplicating by seq. Reason: an event published between the history read and the first read of the stream is not lost (#11).
- **Subscriber queues are bounded and drop the oldest.** Each subscriber has a queue of 256; when it is full the oldest event goes and publishing never waits. Reason: one stalled browser tab must not slow the agent, and the client fills the gap from `history(incident_id, after_seq)` (#11).
- **`subscribe(None)` receives every incident.** Reason: the overview page shows all incidents live (#11).
- **Subscribers unregister on exit or cancel.** Reason: a closed SSE connection must not leave a queue filling forever (#11).
- **Clocks are injected, timestamps are UTC.** Stored as ISO strings. Reason: tests control time and ordering without sleeping (#11).
- **The app shares one `ApprovalStore` and one `GuardLog`.** `build_parts` passes them to the graph, the runner, the API and the reminder loop; the lifespan starts the reminders with the watcher, stops them first, and flushes the guard log after the runs and the watcher stop. Reason: a decision recorded by the API or Telegram must be the one the run and the boot recovery read, and a guard record made while shutting down must not be lost (#21).

## Action log

`incidents/action_log.py` (`ActionLog`), table `action_log` (migration 0009). Execute's ledger, so a crash or restart mid-Execute never runs a step twice (Review Focus 1).

- **One row per step, keyed by incident, plan hash and 0-based step index.** Reason: a loop back to Propose makes a new plan with its own hash, so its steps get rows of their own and the old plan's rows stay as history (#24).
- **`started` is written before the engine call, `done` or `failed` after it; a step after a failed one is `skipped`.** `start` returns the time it wrote, or None when the step already finished, so a finished step can never be started again; starting a step still `started` moves its start time to the new attempt. `finish` changes only a `started` row and keeps the start time written before the call. `skip` never overwrites an existing row, because a started step may already have reached the engine. Reason: a resumed run must tell "never ran" from "may have run" from "ran", and each write is one statement whose result says whether it took (#24).
- **`steps(incident_id)` reads the rows back as `LoggedStep(plan_hash, host_id, status, result: StepResult)`, in the order they were first written.** Reason: Execute decides what to skip or re-check from it, and the dashboard and the report read the same shape the act tools return (#24).
- **The rows reference `incidents (id)`.** Reason: an action is always part of an incident, unlike the guard log, which also records for self-recoveries (#24).

