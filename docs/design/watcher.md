# Watcher: detection

How raw signals become incidents, self-recoveries or nothing. The decision logic is `bleep/watcher/detector.py`: pure, with an injected clock, so it is tested without Docker.

## Pull model: `watcher/poller.py`, `events.py`, `feed.py`

One `HostWatcher` per host ticks every 15 s (`BLEEP_WATCH_INTERVAL_S`). A tick is: reconnect if the host is down, `events(since=cursor, until=now)`, then `Registry.refresh` (which lists containers). It is pure code; no model is called.

- **Pull, not a live event stream.** Reason: Bleep reaches a few hosts over agentless SSH, and some sleep. A dropped link or a sleeping host is a non-event: the tick fails, and the next one reads from the same cursor and catches up. There is no stream state to repair and no reconnect-and-replay race. The limit is up to 15 s of detection delay and one listing per host per tick; at hundreds of hosts an agent on each host would be the right design (#13, decided by Danny 2026-11-02).
- **The cursor advances only after a successful tick,** and a failed tick emits nothing but its probe result. Reason: so a gap is replayed once in full, and a host that was asleep is never marked dead by silence (#13).
- **Events and the listing are fetched before anything is emitted,** and the watcher's state changes only then. Reason: a tick that fails halfway must be exactly repeatable (#13).
- **Event identity is (container id, action, time).** A remembered set, kept 120 s past the cursor, drops events that an overlapping or retried window returns again; the window bounds are inclusive, so the boundary event is returned twice by design. Reason: a retried tick must never emit a signal twice (#13).
- **Mapping.** `die` is `container_died` with exit code and the container's restart policy; `oom` is `oom`; `health_status: unhealthy` is `unhealthy`; a `restart` event with no `die` in the same batch is a `container_died` with no exit code. `restart_loop` stays the detector's. Reason: a restart policy firing is evidence of a death, but a single restart is not by itself a loop (#13).
- **Stopped on purpose is not a death.** A `stop` or `destroy` event for the container in the same batch removes its `die` and `restart`. A container that exited with 0 or 143 between two snapshots makes no signal either; the service shows `stopped`. Reason: `docker stop` and `compose stop` are choices, and a restart policy does not fire for them (#13).
- **`bleep.self` containers emit nothing,** by the event's own labels or the snapshot's (#13).
- **The snapshot diff catches what events missed, once.** A container that was running and is now gone, exited with a crash code, or has a higher restart count is reported as `container_died` on the tick where the change first shows. A death the diff reported is remembered, so a late event for the same death is skipped. Reason: events can be lost (the engine backlog, a window cut short), and listing and events are read a moment apart (#13).
- **Every tick sends `host_unreachable` with `ok` to the detector,** true when the tick reached the host and false when not, so its 5-minute timer starts and resets (#13).
- **The detector is ticked with the containers of the same tick** (`Feed.snapshot`, grouped by service id). Reason: the detector judges a recovery from `started_at` and state, which must come from the snapshot taken with the signal, not from an older one (#13).
- **A start before the death is never a recovery.** `Detector.tick` treats a container whose `started_at` is earlier than the death as the old run seen in a stale snapshot: not recovered, so it opens past grace if it never restarts. Reason: otherwise a snapshot taken just before the death would clear it (#14 review, #13).
- **`Feed` stands in for the manager until Task 15:** it feeds the shared `Detector` and logs its outcomes. Task 15 replaces the logging with incident handling (#13).
- **A tick is bounded to 60 s and never raises;** a sink that fails is logged and the rest of the tick goes on. Reason: one hung call or one bad consumer must not stop a host's watching (#13).
- **Host basics (disk, memory, load) are not fed here.** They have their own cadence (60 s); the detector counts polls, so the 15 s tick must not feed them (#13).

- **Grace window of 60 s.** A container that dies and is running and healthy (or running with no healthcheck) within `container_grace_s` is a `Recovered` event with a one-line explanation, not an incident. Reason: restart policies fix most deaths in seconds, and paging a human for those teaches them to ignore pages (#14).
- **A healthcheck still "starting" is not recovered.** It counts as down past grace once the window ends. Reason: running but not yet healthy proves nothing about the fix (#14).
- **Repeated deaths: 3 in 600 s open an incident, even if each recovered.** Counted per container in a sliding window. Reason: a crash loop that restarts quickly is still broken (#14).
- **One-shot containers are ignored.** Exit code 0 with restart policy `no` or empty is `Ignored("one-shot exit 0")`; exit 0 under a restart policy is a normal death. Reason: migrations and batch jobs are meant to exit, while a service that keeps exiting 0 under `always` is not healthy (#14).
- **Host subjects.** Disk (at or above 90 %), memory (available under 5 % for 3 polls), load (per CPU over 2.0 for 3 polls) and host unreachable (5 min) open on a host subject, not a service. Poll-count thresholds count consecutive breaches per host and reset on a good poll; the watcher feeds every poll, good or bad (`ok`, `percent`, `available_percent`, `load_per_cpu` in the signal detail). Reason: a single spike is noise, and the cause of a host problem is rarely one service (#14).
- **URL checks.** Two consecutive failures open; a reply slower than 5000 ms counts as failed; a good check resets the count (#14).
- **Debounce: open immediately, attach later.** The first qualifying signal for a subject emits `OpenIncident` at once; further qualifying signals for the same subject within 30 s emit `AttachSignals`, so there is never a second open inside the window. Reason: waiting out the window would delay a real outage by 30 s, and the manager only has to handle "create" and "append to the incident I just created" (#14).
- **Pass-through kinds.** `unhealthy`, `restart_loop` and `down_past_grace` already mean a threshold was crossed at the source, so they open directly. `oom` is treated like a death and gets the grace window (#14).
- **Defaults** (`Thresholds` in `schemas/settings.py`): grace 60 s, repeated deaths 3 in 600 s, URL failures 2, slow 5000 ms, disk 90 %, memory under 5 % for 3 polls, load per CPU 2.0 for 3 polls, host unreachable 300 s, debounce 30 s (#14).
- **Recovery explanations.** `explain_recovery` sends the last 4,000 characters of logs, wrapped as untrusted data with any `<untrusted` tags inside escaped, to the cheap model and keeps one sentence of at most 200 characters. Reason: logs are attacker-influenced text and the line goes on the dashboard (#14).
- **Persistent breach re-opens.** Disk at 95 % polled every minute yields an `OpenIncident` each time the 30 s window has passed. The manager dedupes against the subject's open incident (`IncidentRepo.open_or_attach` attaches), so repeats are expected and safe (#14).
- **Late ticks.** A container running healthy (or with no healthcheck) whose `started_at` is within the grace window of its death is recovered whenever `tick` first sees it. One still `starting` cannot be judged that way, so the manager must tick about every 10 s (#14).
- **An `unhealthy` signal for a container with a pending death** folds that death into its incident and clears it, so grace expiry does not open a second one. A second death while one is pending keeps the first death's grace window (#14).
- **Bounded state.** `tick` drops death history older than the repeat window and debounce windows older than `debounce_s` (#14).

## Signal contract for the watcher and manager

| Signal kind | Detail keys | Notes |
| --- | --- | --- |
| `container_died` | `exit_code`, `restart_policy` | `signal.container` must equal `ContainerSummary.name` |
| `oom` | none | treated as a death |
| `unhealthy`, `restart_loop`, `down_past_grace` | none | open directly |
| `url_failed` | `ok`, `latency_ms`, `url` | fed on every check, good or bad; the name means "URL check result" and `ok` says whether it passed (the kind is shared with the schema, so it is not renamed) |
| `disk` | `percent` | fed every poll |
| `memory` | `available_percent` | fed every poll |
| `load` | `load_per_cpu` | fed every poll |
| `host_unreachable` | `ok` | fed on every probe, `ok=True` resets |

`tick(now, states)` takes `states` keyed by service id. The manager must handle `AttachSignals` (append to the incident just opened) as well as `OpenIncident`.
