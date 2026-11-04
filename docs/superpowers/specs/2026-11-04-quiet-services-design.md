# Quiet services and hosts: snooze and leave alone

Date: 2026-11-04. Status: draft for Danny's review.

## Why

While Danny works on a project, or keeps one shut down on purpose, Bleep treats it as an outage.
Today on his laptop molecula and ai-interview show **Down** (a container that ignored SIGTERM was
killed with exit 137, which Bleep cannot tell from an OOM kill), the hero says Bleep could not
finish, and there is no way to say "this is fine". The red is noise, and noise trains him to stop
reading the dashboard, which is the failure Bleep exists to avoid.

He wants to tell Bleep, per service or for a whole host, to stay quiet: for a while, or until he
says otherwise. A whole host: "I don't want my local Docker bothering me any more" (Danny).

## Goals

- A person can make one **service**, or a whole **host**, quiet, in one of two modes:
  - **Snooze**: until a time. Choices: 1 hour, 4 hours, until tomorrow 08:00 (local time of the
    browser).
  - **Leave alone**: until the person switches it back. It never ends on its own, not even when the
    service runs healthily again and later breaks. Danny: "Leave it alone means leave it alone; if
    I forget about it, that's on me."
- While a service is quiet, Bleep opens **no incident** for it and sends **nothing** (dashboard or
  Telegram).
- A quiet service never makes anything look worried: its host tile is not tinted red, the overview
  hero does not name it, Bleep's mood and the header state ignore it. It shows a grey pill
  instead of its health: "Left alone" or "Snoozed until 18:00".
- Other services on the same host stay fully watched. Working on molecula must not hide that shop
  is down. A quiet host never hides anything on another host.
- The switch is reachable from three places on the dashboard:
  1. the service's row on the overview (a small moon button that opens the menu),
  2. the service's page (a "Quiet" button in its header; while quiet, a banner with "Watch again"),
  3. an open incident's page (a third answer beside yes and no, "I'm on it", with the same menu).

  A host's switch sits on its tile header on the overview (the same moon button and menu) and on
  the page of a host incident.

## Non-goals

- **Not in Telegram.** No snooze buttons there (Danny). Telegram only sees the effects: a closed
  incident's notice, and normal incident messages after a snooze ends.
- **Not per container.** A service is the smallest thing that can be quiet.
- **Not one-shot or scheduled jobs.** "Exited 0 is this job's normal state" stays in #84, which
  shrinks to that (and to folding repeats). Its "stopped on purpose" part is replaced by this.
- No custom snooze duration in this version.

## Anti-goals

- A quiet service on one host silencing anything else (another service, the host's own
  unreachable incident, another host).
- Bleep acting on a quiet service. Quiet means no investigation, no proposal, no action.
- A snooze ending into a flood: whatever happened during the snooze must not arrive as a burst of
  incidents or messages afterwards.

## Behaviour

### While quiet

- **Signals are still read, not acted on.** The watcher, URL checks and the detector keep running,
  so health stays true and Bleep knows the real state the moment the quiet ends. Outcomes that
  would open or attach to an incident for the quiet service are dropped with a debug log line.
- **Host incidents are unaffected.** A host that becomes unreachable still opens its host
  incident. (A quiet service's URL failures that would be held for a host incident are dropped like
  its other signals.)
- **Recoveries** ("Fixed themselves") of a quiet service's containers are still recorded; they are
  facts, not alerts.
- **Health in the API** keeps the real value (`down`, `ok`, ...) and adds the quiet state next to
  it, so the dashboard decides how to show it and nothing loses information.

### A quiet host

- A quiet host makes **every service on it** quiet and also silences its own **host incidents**
  (unreachable, disk, memory). This covers a laptop that sleeps (#85) as well.
- A service's own quiet state is **kept underneath** and outlives the host's: "Watch again" on the
  host does not end a service that was left alone or snoozed on its own (my call).
- A service is quiet when it is quiet itself or its host is; the dashboard shows the reason
  ("Left alone" for its own, "Host left alone" or "Host snoozed until 18:00" for the host's).
- On the overview the tile of a quiet host is calm (no tint, a grey pill beside the host name in
  place of its health), its services keep their rows with grey pills, and it never counts as
  hurting.

### Starting quiet while an incident is open

- An unfinished incident on that service is closed through `IncidentRunner.end` (the path host
  removal uses), with the text "You told me to leave molecula alone." or "You snoozed molecula
  until 18:00.". `end` already puts this on the timeline and tells the channels.
- **A plan that is running is never cut short.** While the incident is `executing` or `verifying`,
  the incident page does not offer "I'm on it", and quieting from the overview or the service page
  lets the running step and its check finish. Reason: cancelling a half-run approved plan can leave
  the service worse than either state.
- **It then closes instead of going on.** When that run leaves `executing`/`verifying` and its
  service (or host) is quiet, the incident is closed with the quiet notice rather than resolved
  or sent back to investigate: a failed check would otherwise lead to a new plan and a new approval
  request, which is exactly what quiet rules out. A check that passed still resolves it as fixed
  (my call).
- **Failed runs stop counting.** A failed incident keeps the overall state red for 24 h today
  (`FAILED_NEEDS_YOU_MS` in `lib/mood.ts`, and the "Needs you" list since #209). A failed incident
  whose service or host is quiet now does not: the mood, the header and the "Needs you" list skip
  it. The incident itself is left as it is (my call: history stays true; only its weight on the
  overview changes).

### Ending

- **Snooze** ends at its time. **Leave alone** ends only by "Watch again" (service page banner, or
  the moon menu, which then shows "Watch again" in place of the choices).
- **At the end Bleep looks at the service as it is now.** If its health is `down` at that moment
  (a crashed container, failing URLs), it opens one incident for it, as a fresh signal, and
  Telegram hears about it as usual. `degraded`, `stopped` or `ok` open nothing. The detector alone
  would not do this: a container that died during the snooze and stays dead produces no new
  outcome, so this check is needed (molecula's exit 137 is exactly this case).
- This is not a replay: what happened during the quiet is not reported, only the state at the end.
- The check runs on "Watch again" and when a snooze's time passes, so expiry needs something that
  fires (a periodic task, about every 30 s, is enough); checking expiry only when the API is read
  is not. For a host that ends, every service that is not still quiet on its own is checked, and
  an unreachable host opens its host incident.
- Changing a snooze (for example from 1 hour to leave alone) replaces it; there is one quiet state
  per service at most.

### On the dashboard

- **Pill**: grey (the `stop` tone), moon icon, text "Left alone" or "Snoozed until HH:MM"
  ("until tomorrow 08:00" when it ends on another day). It replaces the health pill on the
  overview row and on the service page header.
- **Overview tile**: a host whose only problems are quiet services is not tinted and does not
  count as hurting; the "N of M ..." reach line does not count quiet services as hurting.
- **Hero and mood**: they already follow incidents; with no incidents for quiet services they
  calm down by themselves. Where the hero reads service health (`heroSituation`), quiet services
  count as fine.
- **Moon button on the overview row**: visible on hover and focus on desktop, always on touch
  screens. The menu: "Snooze <service>" label, "For 1 hour", "For 4 hours", "Until tomorrow 8:00",
  a divider, "Leave it alone" ("until you switch it back"). When quiet, the menu shows the current
  state and "Watch again".
- **Service page**: "Quiet" button in the header with the same menu; while quiet, a grey banner
  "Left alone since 14:02. Bleep opens no incidents for it." (or "Snoozed until 18:00.") with
  "Watch again".
- **Incident page**: "I'm on it" beside "Yes, go ahead" and "No, leave it", with the same menu,
  on incidents whose subject is a service and that are not executing or verifying. Choosing closes
  the incident as above.
- The menu is a Base UI menu or popover from the same shadcn base-nova set as the dialog (#218).

## Data and API (the builder's call, within these constraints)

- The quiet state lives in the database (it is state a person sets, not a secret): service id,
  mode (`snooze` or `leave_alone`), `until` (snooze only), when it was set. A new migration.
  A snooze past its `until` counts as not quiet whether or not a cleanup has removed the row yet.
- A small API: set (mode, until) and clear, per service id and per host id; the service and host
  views carry the quiet state (their own, and for a service whether its host's applies). The server, not the browser, decides "is quiet now", against its own clock; the
  browser sends the snooze end as an absolute time.
- The check sits in one place that every incident-opening path goes through, for service and host
  subjects (today `WatcherManager._open` and the held-URL path both end in
  `IncidentRepo.open_or_attach`).
- Layering stays as in `CLAUDE.md`: `api` → `incidents`; the watcher asks a small quiet-state
  reader, it does not import `api`.

## Done when

- A quiet service opens no incident and sends nothing, for container deaths, restart loops,
  unhealthy, OOM and failing URLs; a non-quiet service on the same host still does.
- A host-unreachable incident still opens while one of its services is quiet; a quiet host opens
  none, and none of its services opens one either; another host is unaffected.
- "Watch again" on a host leaves a service's own quiet state in force.
- Quieting a service closes its open incident with the notice, except one that is executing or
  verifying, which finishes its step and check first and is then closed (or resolved if the check
  passed), never sent back to investigate.
- A failed incident of a quiet service or host does not make the mood, the header or the "Needs
  you" list red.
- A snooze ends at its time without anyone reading the API, and a service that is `down` then
  opens exactly one incident; "Watch again" does the same; leave alone never ends by itself.
- The overview (service rows and host tiles), the service page and the incident page offer the
  switch as described; the pill, tile, reach line and hero treat quiet services and hosts as calm.
- Tests cover each of the above (fakes, no network), and `docs/design/` gets the bullets
  (watcher, incidents, app), with `docs/architecture.md` if the overview changes.

## Left to the builder

- Table and column names, the API paths and the pydantic models.
- Whether the expiry is checked on read only or also cleaned up by a periodic task.
- How the issue is split (backend, then the dashboard is the natural cut).
