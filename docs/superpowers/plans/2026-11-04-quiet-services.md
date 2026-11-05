# Quiet Services and Hosts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can snooze a service or a whole host, or leave it alone until they switch it back, so Bleep opens no incident for it, sends nothing and shows it calm; when the quiet ends, Bleep looks at it as it is then.

**Architecture:** A `QuietStore` (SQLite, `incidents/quiet.py`) holds one quiet state per service or host and tells subscribers when one starts or ends; a 30 s loop ends expired snoozes. The `WatcherManager` drops detector outcomes for quiet subjects, closes open incidents when a quiet starts and re-judges the subject when it ends. The graph's node wrapper stops a run at any node before Execute once its subject is quiet, so a failed check never leads to a new plan. The API carries the quiet state on the service and host views; the dashboard shows grey pills and a moon menu on the overview, the service page and the incident page.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, LangGraph (backend); Next.js 16, React 19, Tailwind v4, Base UI via shadcn base-nova, Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-04-quiet-services-design.md`

## Global Constraints

- Follow `CLAUDE.md`: layout, dependency direction (`api` → `incidents` → `graph` → `tools`; `watcher` → `incidents`), no prompt text in Python, ruff line length 100, typed errors, comments only where the flow is not obvious.
- Tests never touch the network or a real Docker engine (fakes from `tests/fakes.py`, `httpx.MockTransport`, the `Rig` in `tests/watcher/test_manager.py`).
- Modes are exactly `snooze` and `leave_alone`. Scopes are exactly `service` and `host`.
- Snooze choices on the dashboard: "For 1 hour", "For 4 hours", "Until tomorrow 8:00" (browser local time). The browser sends `until` as an absolute UTC ISO time; the server decides "quiet now" against its own clock.
- Leave alone never ends by itself.
- A snooze `until` must be in the future and at most 7 days ahead (422 otherwise); `leave_alone` must not carry `until` (422).
- The expiry loop runs every 30 s (`QUIET_TICK_S = 30`).
- Notice texts (exact): starting quiet on a service — `Closed: you told me to leave {name} alone.` / `Closed: you snoozed {name}; I look at it again when the snooze ends.`; on a host — the same with the host's name. The time is left out of the server's text because the server does not know the browser's time zone (deviation from the spec's "until 18:00", which the dashboard pill shows instead).
- When a quiet ends, only a `down` service (or an unreachable host) opens an incident; `degraded`, `stopped`, `ok`, `unknown` open nothing.
- An incident that is `executing` or `verifying` is never closed by a quiet starting; its run finishes the step and the check.
- Commit messages: one plain sentence, what and why, ending with the issue number; no AI attribution lines.

## Review Focus

1. **A service that is quiet only through its host**: every path (gate, views, pills, mood) must treat it as quiet; tests in Tasks 2, 5 and 7 use a host-quiet case, not only a service-quiet one.
2. **Ending a host's quiet while one of its services is still quiet on its own**: that service must stay quiet and must not be re-judged (Task 3 test).
3. **Setting quiet twice** (snooze, then leave alone; or snooze again with a new time): the second replaces the first, one row, and no second round of closing notices on an incident already closed (Task 1 and Task 2 tests).
4. **A service removed from the host while quiet** (Compose project taken down): the API must not 500, the view simply has no row for it, and the stored state does no harm (Task 5 test).
5. **A snooze whose time passes while the backend is down**: on the next start the loop ends it at once and the re-judging runs (Task 3 test with the clock past `until` before `run_once`).

---

## Issue split

- **Backend issue** (Tasks 1–5): quiet store, gating, end check, graph stop, API. Ships alone: the API can be exercised with curl.
- **Dashboard issue** (Tasks 6–8): menu, pills, overview, service page, incident page, mood.
- Also edit #84 so it keeps only one-shot/scheduled jobs and repeat folding; its "stopped on purpose" part points at these two issues.

---

### Task 1: The quiet store

**Files:**
- Create: `backend/src/bleep/store/migrations/0020_quiet.sql`
- Create: `backend/src/bleep/schemas/quiet.py`
- Create: `backend/src/bleep/incidents/quiet.py`
- Modify: `backend/src/bleep/wiring.py` (`AppParts.quiet`, built in `build_parts`)
- Test: `backend/tests/incidents/test_quiet.py`

**Interfaces:**
- Produces (`schemas/quiet.py`):
  - `QuietMode = Literal["snooze", "leave_alone"]`, `QuietScope = Literal["service", "host"]`
  - `class QuietState(BaseModel): scope: QuietScope; target_id: str; mode: QuietMode; until: datetime | None; since: datetime`
  - `class QuietRequest(BaseModel): mode: QuietMode; until: datetime | None = None` (validation lives in the API, Task 5)
  - `@dataclass(frozen=True) class QuietChange: kind: Literal["started", "ended"]; state: QuietState`
- Produces (`incidents/quiet.py`):
  - `class QuietStore(db: Database, clock: Callable[[], datetime] = utcnow)`
  - `async def set(self, scope: QuietScope, target_id: str, mode: QuietMode, until: datetime | None) -> QuietState` — upsert; notifies `started`
  - `async def clear(self, scope: QuietScope, target_id: str) -> bool` — False when nothing was set; notifies `ended`
  - `async def active(self) -> list[QuietState]` — rows not past `until` by the clock
  - `async def of(self, scope: QuietScope, target_id: str) -> QuietState | None` — active only
  - `async def for_subject(self, subject: Subject) -> QuietState | None` — the host's state wins over the service's own when both are active (so the dashboard says "Host left alone"); a service subject checks its host and itself, a host subject checks the host
  - `async def is_quiet(self, subject: Subject) -> bool`
  - `async def expire(self) -> list[QuietState]` — deletes rows past `until`, notifies `ended` for each, returns them
  - `def subscribe(self, listener: Callable[[QuietChange], Awaitable[None]]) -> None`
  - `async def run(self, interval_s: float = QUIET_TICK_S) -> None` — loops `expire()`; a failing tick is logged and the loop goes on

- [ ] **Step 1: Write the migration**

`quiet` table: `scope TEXT NOT NULL`, `target_id TEXT NOT NULL`, `mode TEXT NOT NULL`, `until TEXT` (NULL for leave alone), `since TEXT NOT NULL`, `PRIMARY KEY (scope, target_id)`. A comment line saying what it holds (#issue).

- [ ] **Step 2: Write the failing tests** in `tests/incidents/test_quiet.py` (a `Clock` like the manager tests' and a migrated `Database(tmp_path / "bleep.sqlite")`):

```python
async def test_a_snooze_is_quiet_until_its_time_and_then_not(store, clock): ...
    # set("service", "h/compose:shop", "snooze", T0 + 1h); is_quiet(service subject) True;
    # clock.advance to T0 + 1h + 1s; is_quiet False; of(...) is None
async def test_leave_alone_has_no_end(store, clock): ...  # advance 365 days, still quiet
async def test_a_quiet_host_makes_its_services_quiet_but_not_another_hosts(store): ...
async def test_for_subject_names_the_host_when_both_are_quiet(store): ...  # state.scope == "host"
async def test_setting_again_replaces_the_state(store): ...  # snooze then leave_alone: one row, mode leave_alone
async def test_listeners_hear_started_ended_and_expiry(store, clock): ...
    # set -> [started]; clear -> [ended]; set snooze, advance past, expire() -> [ended] once;
    # a second expire() hears nothing
async def test_clear_of_nothing_returns_false_and_tells_nobody(store): ...
async def test_a_failing_listener_does_not_stop_the_others(store): ...
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/incidents/test_quiet.py -q` (in `backend/`). Expected: import error for `bleep.incidents.quiet`.

- [ ] **Step 4: Implement `schemas/quiet.py` and `incidents/quiet.py`** with the interfaces above; listeners are called in order, each in its own `try`/`except Exception` with `log.exception`. Add `quiet: QuietStore` to `AppParts` and build it in `build_parts` next to `incidents`.

- [ ] **Step 5: Run the tests**: `uv run pytest tests/incidents/test_quiet.py tests/store -q`. Expected: pass.

- [ ] **Step 6: Commit** — `Store quiet states for services and hosts and tell listeners when one starts or ends (#220)`

---

### Task 2: Quiet subjects open nothing, and starting quiet closes their incidents

**Files:**
- Modify: `backend/src/bleep/watcher/manager.py` (`__init__` gets `quiet: QuietStore | None = None`; `_open`, `_hold`, `_attach_held`; new `quiet_changed`)
- Modify: `backend/src/bleep/main.py` (pass `parts.quiet`; `parts.quiet.subscribe(manager.quiet_changed)`)
- Test: `backend/tests/watcher/test_manager.py`

**Interfaces:**
- Consumes: `QuietStore.is_quiet`, `QuietStore.subscribe`, `QuietChange` (Task 1); `IncidentRunner.end(incident_id, text)`; `IncidentRepo.open_incident_id(subject)`, `IncidentRepo.open_on_host(host_id)`.
- Produces: `async def WatcherManager.quiet_changed(self, change: QuietChange) -> None` — on `started`: ends every unfinished incident of the subject (a host: every one in `open_on_host`), except those `executing` or `verifying`, with the notice from Global Constraints; on `ended`: Task 3.
- Produces: `def quiet_notice(name: str, state: QuietState) -> str` in `watcher/manager.py`.

- [ ] **Step 1: Write the failing tests** (extend `Rig` with `self.quiet = QuietStore(self.db, self.clock)` passed to the manager):

```python
async def test_a_quiet_service_opens_no_incident_for_a_death(tmp_path): ...
    # quiet "h/compose:shop" leave_alone; kill shop-web-1 past grace; await r.incidents() == []
async def test_a_quiet_service_does_not_hide_another_service_on_the_host(tmp_path): ...
    # shop quiet, kill molecula-web-1: one incident, subject molecula
async def test_a_quiet_host_opens_nothing_for_its_services_or_itself(tmp_path): ...
    # host "h" snoozed; a death and a 5-minute outage: no incidents
async def test_a_quiet_services_url_failures_are_not_held_for_the_host_incident(tmp_path): ...
    # shop quiet; host lost 5 min: host incident opens, its signals hold no shop url_failed
async def test_quieting_a_service_closes_its_open_incident_with_a_notice(tmp_path): ...
    # open via a death; quiet.set(...); incident closed; runner.ended == [(id, "Closed: you told me to leave shop alone.")]
async def test_quieting_leaves_an_executing_incident_to_finish(tmp_path): ...
    # repo.set_status(id, "executing"); quiet.set(...); not ended
async def test_snoozing_again_does_not_end_a_closed_incident_twice(tmp_path): ...
```

`FakeRunner` in the test file gets an `ended: list[tuple[str, str]]` and an `async def end(self, incident_id, text)` that records and calls `repo.close_unfinished`.

- [ ] **Step 2: Run them to see them fail**: `uv run pytest tests/watcher/test_manager.py -q -k quiet`. Expected: FAIL (incidents opened; `quiet_changed` missing).

- [ ] **Step 3: Implement.** `_open` returns early (debug log naming the subject key) when `await self._quiet_now(subject)`; `_hold` drops a signal whose subject is quiet; `quiet_changed(started)` resolves the name from the registry (service name, or host name) and calls `self.runner.end` for each matching unfinished incident whose status is not `executing`/`verifying`. With `quiet=None` (other tests) nothing is quiet. Wire it in `main.py` after `manager` is built.

- [ ] **Step 4: Run** `uv run pytest tests/watcher -q`. Expected: pass, old tests included.

- [ ] **Step 5: Commit** — `Open no incident for a quiet service or host, and close its open one when the quiet starts (#220)`

---

### Task 3: When a quiet ends, judge the subject as it is now

**Files:**
- Create: `backend/src/bleep/watcher/health.py` (moved, unchanged: `CLEAN_EXITS`, `_RANK`, `_worst`, `is_finished`, `is_stopped`, `container_health`, `service_health`, `host_health`, `STALE_AFTER`, `url_status` (was `_url_status`))
- Modify: `backend/src/bleep/api/hosts.py` (imports them from `watcher/health.py`; keeps the names importable from `bleep.api.hosts` so `tests/api/test_hosts.py` is untouched)
- Modify: `backend/src/bleep/watcher/manager.py` (`quiet_changed` for `ended`; `recheck`)
- Modify: `backend/src/bleep/main.py` (start `parts.quiet.run()` as a task after `manager.start()`, cancelled on shutdown like the reminders)
- Test: `backend/tests/watcher/test_manager.py`

**Interfaces:**
- Consumes: Task 1 and 2; `UrlCheckStore.latest(host_id)`; `Registry.services(host_id)`, `Registry.hosts()`.
- Produces: `async def WatcherManager.recheck(self, subject: Subject) -> None` — a service subject: if it is still quiet (its own state or its host's), nothing; if its `service_health` (containers plus fresh URL checks) is `down`, `_open(subject, signals)` with one `down_past_grace` signal per `down` container (`container=` its name, `detail={"state": ..., "exit_code": ...}`) and one `url_failed` per failing URL (`detail={"url": ...}`); a host subject: if unreachable, `_open` a host subject with one `host_unreachable` signal (`detail={"ok": False}`), then `recheck` each of its services.

- [ ] **Step 1: Write the failing tests:**

```python
async def test_a_service_still_down_when_its_snooze_ends_opens_one_incident(tmp_path): ...
    # snooze shop 1h; kill shop-web-1 (exit 137) past grace -> no incident;
    # advance past until; await r.quiet.expire(); one incident on shop, opened once
async def test_watch_again_on_a_healthy_service_opens_nothing(tmp_path): ...
async def test_ending_a_hosts_quiet_leaves_a_service_quiet_on_its_own(tmp_path): ...
    # host left alone, shop left alone too; shop dies; clear host -> no shop incident
async def test_ending_a_hosts_quiet_while_it_is_unreachable_opens_the_host_incident(tmp_path): ...
async def test_a_snooze_that_ended_while_bleep_was_down_is_ended_on_the_first_tick(tmp_path): ...
    # set snooze, advance 2h before any expire(); one expire() ends it and opens the incident
```

- [ ] **Step 2: Run them**: `uv run pytest tests/watcher/test_manager.py -q -k "ends or again or ending"`. Expected: FAIL.

- [ ] **Step 3: Move the health functions** to `watcher/health.py` and re-import them in `api/hosts.py`. Run `uv run pytest tests/api/test_hosts.py -q`: pass unchanged.

- [ ] **Step 4: Implement `recheck`** and the `ended` branch of `quiet_changed` (it builds the subject from the state: a host scope → `Subject(kind="host", host_id=target_id)`; a service scope → its host from the registry's service, skipped when the service is gone). Start the expiry loop in `main.py`.

- [ ] **Step 5: Run** `uv run pytest tests/watcher tests/api/test_hosts.py tests/test_main.py -q`. Expected: pass.

- [ ] **Step 6: Commit** — `Look at a service or host again when its quiet ends and open one incident if it is still down (#220)`

---

### Task 4: A run stops before any new step once its subject is quiet

**Files:**
- Modify: `backend/src/bleep/graph/deps.py` (`class IncidentQuiet(Exception)`; `class QuietCheck(Protocol): async def is_quiet(self, subject: Subject) -> bool`; `class NeverQuiet`; `GraphDeps.quiet: QuietCheck = field(default_factory=NeverQuiet)`)
- Modify: `backend/src/bleep/graph/build.py` (`_traced` gets `quiet: QuietCheck`; `QUIET_STOPS = frozenset({"gather", "investigate", "diagnose", "propose", "approval"})`)
- Modify: `backend/src/bleep/incidents/runner.py` (`_drive` catches `IncidentQuiet`)
- Modify: `backend/src/bleep/wiring.py` (`GraphDeps(..., quiet=quiet_store)`)
- Test: `backend/tests/graph/test_build.py`, `backend/tests/incidents/test_runner.py`

**Interfaces:**
- Consumes: `QuietStore` satisfies `QuietCheck` (Task 1).
- Produces: `IncidentQuiet(incident_id: str)`; `QUIET_RUN_NOTICE = "Closed: the service was made quiet while I worked on it, so I stopped here."` in `runner.py` (for a host subject, "the host").

- [ ] **Step 1: Write the failing tests:**

```python
# tests/graph/test_build.py
async def test_a_quiet_subject_stops_the_run_before_the_next_node(...): ...
    # deps with a QuietCheck returning True; invoking from START raises IncidentQuiet before gather runs
async def test_execute_verify_and_report_run_on_while_quiet(...): ...
    # the wrapper for "verify" with quiet True runs the node

# tests/incidents/test_runner.py
async def test_a_failed_check_on_a_quiet_service_closes_the_incident_instead_of_a_new_plan(...): ...
    # graph whose verify fails -> investigate; quiet turns True after approval;
    # status "closed", notice QUIET_RUN_NOTICE on the bus, notifier.notice called once,
    # no second approvals.request
async def test_a_passed_check_on_a_quiet_service_still_resolves(...): ...
```

- [ ] **Step 2: Run them**: `uv run pytest tests/graph/test_build.py tests/incidents/test_runner.py -q -k quiet`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `_traced`, for a name in `QUIET_STOPS`, check `await quiet.is_quiet(state["subject"])` before setting the status and raise `IncidentQuiet(state["incident_id"])`. In `_drive`, `except IncidentQuiet:` → `close_unfinished`, publish the notice, `_tell`, forget pauses, return.

- [ ] **Step 4: Run** `uv run pytest tests/graph tests/incidents -q`. Expected: pass.

- [ ] **Step 5: Commit** — `Stop an incident's run before any new step once its service or host is quiet, so a failed check never asks again (#220)`

---

### Task 5: The quiet API and quiet on the views

**Files:**
- Create: `backend/src/bleep/api/quiet.py`
- Modify: `backend/src/bleep/main.py` (include the router under `/api`, behind the session like the others; the `services/{id:path}` route ordering note in `get_service` applies: register `/quiet/...` before nothing conflicting)
- Modify: `backend/src/bleep/schemas/api.py` (`QuietView`; `ServiceView.quiet: QuietView | None = None`; `HostView.quiet: QuietView | None = None`; `HostView.quiet_service_ids: list[str] = []`)
- Modify: `backend/src/bleep/api/hosts.py` (`_view`, `_service_views`, `list_hosts`: one `parts.quiet.active()` read per request; `host_health` over non-quiet services only)
  (deliberate: a service view keeps its real `health` next to `quiet`, as the spec says, but a host's `health` is judged over its non-quiet services, since the host is what the overview tints; a quiet host keeps its real health and carries `quiet`)
- Modify: `docs/design/watcher.md`, `docs/design/incidents.md`, `docs/design/app.md` (Health rules), `docs/architecture.md` (overview line if it lists what opens incidents)
- Test: `backend/tests/api/test_quiet.py`, `backend/tests/api/test_hosts.py`

**Interfaces:**
- Consumes: Task 1.
- Produces (HTTP):
  - `PUT /api/quiet/services/{service_id:path}` body `QuietRequest` → 200 `QuietView`; 404 unknown service; 422 bad mode/until
  - `DELETE /api/quiet/services/{service_id:path}` → 204 (also when nothing was set)
  - `PUT /api/quiet/hosts/{host_id}` and `DELETE /api/quiet/hosts/{host_id}` the same for hosts
  - `class QuietView(BaseModel): scope: QuietScope; mode: QuietMode; until: datetime | None; since: datetime` — on a service, `scope="host"` means it is quiet through its host

- [ ] **Step 1: Write the failing tests:**

```python
def test_put_snooze_then_the_service_view_carries_it(client): ...
def test_leave_alone_with_until_is_422_and_snooze_in_the_past_is_422(client): ...
def test_snooze_more_than_seven_days_ahead_is_422(client): ...
def test_unknown_service_or_host_is_404(client): ...
def test_a_quiet_host_marks_every_service_and_lists_them_in_quiet_service_ids(client): ...
def test_a_host_whose_only_down_service_is_quiet_is_ok(client): ...
def test_a_quiet_state_for_a_service_that_is_gone_does_not_break_the_lists(client): ...
def test_delete_is_204_twice(client): ...
def test_the_routes_need_a_session(client): ...
```

- [ ] **Step 2: Run them**: `uv run pytest tests/api/test_quiet.py -q`. Expected: FAIL (404 route).

- [ ] **Step 3: Implement** the router (validation: `snooze` needs `until` with a time zone, `now < until <= now + 7 days`; `leave_alone` refuses `until`; a 422 names the field like the settings routes do) and the view fields. Write the doc bullets (each with its reason and the issue number), including the notice-text deviation from Global Constraints.

- [ ] **Step 4: Run the whole backend**: `uv run pytest -q && uv run ruff check && uv run ruff format --check`. Expected: pass.

- [ ] **Step 5: Commit** — `Let the dashboard set and clear a quiet state and show it on the service and host views (#220)`

---

### Task 6: Types, API client, the pill and the moon menu

**Files:**
- Create: `frontend/components/ui/dropdown-menu.tsx` (shadcn base-nova `dropdown-menu` from `npx shadcn@latest view dropdown-menu`, restyled with the Bleep tokens like `components/ui/dialog.tsx`)
- Create: `frontend/lib/quiet-api.ts`, `frontend/components/quiet/quiet-pill.tsx`, `frontend/components/quiet/quiet-menu.tsx`
- Modify: `frontend/lib/api-types.ts` (`QuietView`, `ServiceView.quiet`, `HostView.quiet`, `HostView.quiet_service_ids`)
- Modify: `frontend/components/overview/fixtures.ts` (defaults `quiet: null`, `quiet_service_ids: []`)
- Test: `frontend/components/quiet/quiet-menu.test.tsx`, `frontend/components/quiet/quiet-pill.test.tsx`

**Interfaces:**
- Produces:
  - `type QuietTarget = { scope: "service" | "host"; id: string; name: string }`
  - `setQuiet(target: QuietTarget, body: { mode: "snooze"; until: string } | { mode: "leave_alone" }): Promise<QuietView>`; `clearQuiet(target: QuietTarget): Promise<void>`
  - `snoozeUntil(choice: "1h" | "4h" | "morning", now: Date): Date` — "morning" is 08:00 local on the next day
  - `quietLabel(quiet: QuietView, now: Date): string` — `"Left alone"`, `"Snoozed until 18:00"`, `"Snoozed until tomorrow 8:00"`; prefixed `"Host "` (`"Host left alone"`, `"Host snoozed until …"`) when `quiet.scope === "host"` on a service
  - `<QuietPill quiet={QuietView} />` — `stop` tone, moon icon, `quietLabel`
  - `<QuietMenu target={QuietTarget} quiet={QuietView | null} onChanged={() => void} trigger?: ReactElement />` — default trigger is a round 44 px moon button labelled `Quiet ${name}`; items "For 1 hour", "For 4 hours", "Until tomorrow 8:00", divider, "Leave it alone" ("until you switch it back"); when `quiet` is set and its scope equals the target's, the menu shows the label and "Watch again"; when the service is quiet through its host, the menu says so and offers no "Watch again" for the host there

- [ ] **Step 1: Write the failing tests:**

```tsx
it("snoozeUntil gives 1 h, 4 h and the next 08:00 in local time")
it("quietLabel says Left alone, Snoozed until HH:MM, tomorrow, and Host … for a host's quiet")
it("choosing For 4 hours PUTs a snooze with until four hours ahead and calls onChanged")
it("Leave it alone PUTs leave_alone without until")
it("a quiet service's menu offers Watch again, which DELETEs")
it("a service quiet through its host offers no Watch again for the host")
it("a failed PUT shows the API's words and keeps the menu usable")
```

- [ ] **Step 2: Run them**: `npx vitest run components/quiet` (in `frontend/`). Expected: FAIL.

- [ ] **Step 3: Implement.** `@base-ui/react` is already installed (#218); no new dependency.

- [ ] **Step 4: Run** `npm run format:check && npm run lint && npm run typecheck && npx vitest run components/quiet`. Expected: pass.

- [ ] **Step 5: Commit** — `Add the quiet pill and the moon menu that snoozes or leaves a service or host alone (#221)`

---

### Task 7: The overview and the mood treat quiet as calm

**Files:**
- Modify: `frontend/components/overview/host-tile.tsx` (`ServicePill` row: `QuietMenu` button before the pill, visible on hover/focus on `pointer-fine`, always on `pointer-coarse`; `QuietPill` in place of `HealthBadge` when `service.quiet`; tile header: `QuietMenu` for the host and `QuietPill` in place of the host's health)
- Modify: `frontend/components/overview/situation.ts` (`isHurting` false for a quiet host; `serviceHurting` false for a quiet service; `isQuietSubject(subject, hosts)`)
- Modify: `frontend/lib/mood.ts` (`overallMood`/`overallState` take `hosts: readonly Pick<HostView, "id" | "reachable" | "quiet" | "quiet_service_ids">[]`; a quiet host's unreachability and a failed incident whose subject is quiet do not count)
- Modify: `frontend/components/incident/incident-groups.ts` (a failed incident of a quiet subject is not "needs_you"; its group takes `hosts`)
- Modify: `frontend/components/overview/overview.tsx` (pass `onChanged` = the `reload` from #218 to the menus)
- Test: `situation.test.ts`, `lib/mood.test.ts`, `incident-groups.test.ts`, `components/overview/overview.test.tsx`

**Interfaces:**
- Consumes: Task 6.
- Produces: `isQuietSubject(subject: Subject, hosts: readonly Pick<HostView, "id" | "quiet" | "quiet_service_ids">[]): boolean` in `lib/mood.ts` (used by `situation.ts` and `incident-groups.ts`).

- [ ] **Step 1: Write the failing tests:**

```ts
it("a failed incident of a quiet service does not make the mood needs_you")
it("a failed incident of a service quiet through its host does not either")
it("an unreachable quiet host does not make the mood working")
it("a host whose only hurting service is quiet is not hurting and the reach line says it is fine")
it("the Needs you group leaves out a failed incident of a quiet subject")
it("a quiet service row shows Snoozed until 18:00 instead of Down and has a Quiet button")
it("a quiet host's tile is not tinted and shows Host left alone on its services")
it("choosing Leave it alone on a row reads the overview again")
```

- [ ] **Step 2: Run them**: `npx vitest run components/overview lib components/incident/incident-groups.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement**; every existing caller of `overallMood`/`overallState` passes the hosts it already has.

- [ ] **Step 4: Run** `npm test`. Expected: pass.

- [ ] **Step 5: Commit** — `Show quiet services and hosts calm on the overview and leave them out of Bleep's mood (#221)`

---

### Task 8: The service page and the incident page

**Files:**
- Modify: `frontend/components/service/service-page.tsx` (header: `QuietMenu` with trigger "Quiet ▾" as an outline button; `QuietPill` for the health while quiet; a grey banner `"Left alone since 14:02. Bleep opens no incidents for it."` / `"Snoozed until 18:00. Bleep opens no incidents for it."` / `"Its host is left alone. …"` with "Watch again" for the service's own state)
- Modify: `frontend/components/incident/incident-page.tsx` (an "I'm on it ▾" `QuietMenu` trigger for unfinished incidents whose status is not `executing`, `verifying`; target is the subject: the service, or the host for a host incident; after a choice the page reloads and shows the closing notice)
- Modify: `docs/design/app.md` (Dashboard bullets with reasons, #221), `docs/setup.md` if it describes stopping a project
- Test: `components/service/service-page.test.tsx`, `components/incident/incident-page.test.tsx`

**Interfaces:**
- Consumes: Task 6; `ServiceDetail` gets its `quiet` through `ServiceView` (Task 5).

- [ ] **Step 1: Write the failing tests:**

```tsx
it("the service page offers Quiet and, while left alone, a banner with Watch again")
it("Watch again on the banner DELETEs and reloads")
it("a service quiet through its host shows the host banner without Watch again")
it("an awaiting incident offers I'm on it; choosing Leave it alone PUTs for its service")
it("an executing or verifying incident offers no I'm on it")
it("a host incident's I'm on it targets the host")
```

- [ ] **Step 2: Run them**: `npx vitest run components/service components/incident`. Expected: FAIL.

- [ ] **Step 3: Implement** and write the doc bullets.

- [ ] **Step 4: Run all frontend checks**: `npm run format:check && npm run lint && npm run typecheck && npm test && npm run build`; and `uv run --project backend python scripts/check_docs.py` from the repo root. Expected: pass.

- [ ] **Step 5: Commit** — `Snooze or leave a service alone from its page and from an open incident (#221)`
