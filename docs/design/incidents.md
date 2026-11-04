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

## Statuses

An incident's status says what Bleep is doing, in this order for a fix that is approved: `open` (just opened) → `investigating` (Gather, Investigate, Diagnose, Propose) → `awaiting_approval` (stopped at the interrupt) → `executing` (a yes was delivered; Execute runs) → `verifying` (Verify runs) → `resolved` or `closed`. `failed` is the end of a run that raised an error.

- **The graph sets the status for the node it is about to run, before it publishes `node_started`.** `graph/build.py` maps gather, investigate, diagnose and propose to `investigating`, execute to `executing` and verify to `verifying`; approval and report set nothing. Reason: the dashboard reads the incident again on every event, so the status must already be written when `node_started` reaches it; the runner could only react to the event afterwards, and the graph already holds the incident repository in its deps, so this adds no new dependency (#101).
- **The runner sets the status when it starts driving a run.** A yes sets `executing` at once; a new run, a restart from a checkpoint, a no and an instruction set `investigating`, and the next node corrects it. Reason: an approval must never flash back to `investigating`, and a loop back from Execute, Approval or Verify to Propose or Investigate shows as `investigating` again because those nodes set it (#101).
- **`awaiting_approval`, `resolved`, `closed` and `failed` are the runner's.** Reason: only the runner knows the run stopped at the interrupt or finished after its last checkpoint (#16, #28).

## Action log

`incidents/action_log.py` (`ActionLog`), table `action_log` (migrations 0009 and 0012). Execute's ledger, so a crash or restart mid-Execute never runs a step twice (Review Focus 1).

- **One row per step, keyed by incident, plan key and 0-based step index.** The plan key is `<plan hash>:<approval id>`. Reason: a loop back to Propose makes a new plan with its own hash, so its steps get rows of their own and the old plan's rows stay as history (#24), and the same plan approved a second time (after Verify's loop-back) has the same hash and must run again ([graph](graph.md#execute), #25). The column was `plan_hash` until migration 0012 renamed it to `plan_key` for what it holds; existing rows keep their values (#87).
- **`started` is written before the engine call, `done` or `failed` after it; a step after a failed one is `skipped`.** `start` returns the time it wrote, or None when the step already has a row, so a step can never be started twice: not when it finished, and not when it is still `started`, since it may have reached the engine before a crash (#87). `finish` changes only a `started` row and keeps the start time written before the call. `skip` never overwrites an existing row, because a started step may already have reached the engine. Reason: a resumed run must tell "never ran" from "may have run" from "ran", and each write is one statement whose result says whether it took (#24).
- **`steps(incident_id)` reads the rows back as `LoggedStep(plan_key, host_id, status, result: StepResult)`, in the order they were first written.** Only Execute reads it, to decide what to skip, re-check or confirm. The dashboard reads the `action` events and the report reads `step_results`, both in the act tools' `StepResult` shape. Reason: the ledger exists for crash safety; the bus history already holds every step in order (#24, #87).
- **The rows reference `incidents (id)`.** Reason: an action is always part of an incident, unlike the guard log, which also records for self-recoveries (#24).

