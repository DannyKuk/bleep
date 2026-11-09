# Telegram: one thread per incident (#380)

## Why

In a chat with several incidents every message looked the same ("Incident 01M7…: container died
on laptop/compose:shop"), so it was unclear which message started a new problem, which belonged
to one already running, which ended it and how, and that the third incident was the same broken
container again. It felt like an overload of messages with no clear cut between incidents.

Goal: from the chat alone, and with several incidents interleaved, the person can tell for every
message which incident it belongs to and what kind of moment it is (start, your call, end, repeat),
and can jump back to the incident's start from any message.

Non-goals: stopping repeats (#361), German (#375, which translates this shape afterwards), the
dashboard's pages, talking to Bleep in free text (its own issue), an "I'm on it" snooze button
(its own issue).

Anti-goals: more messages than today; a message whose meaning depends on an edit (Telegram does
not notify on edits); anything that weakens the approval rules (buttons, reply-as-instruction,
plans shown in full).

## The shape (chosen: "B")

1. **Every incident has a short name**: the subject plus a running number per subject, e.g.
   `shop #14` for the service `shop`, `M1 #3` for the host `M1`. It replaces the 20-character
   incident id in every message.
2. **The opener is the only message of an incident that is not a reply.** It starts with 🔴 and
   `New: <short name> · <host>`, then one line of what was seen.
3. **Every later message of the incident is a Telegram reply to its opener** (`reply_parameters`
   with `allow_sending_without_reply`, so a deleted opener does not lose the message). Telegram
   shows the opener as a quote on top; tapping it jumps there.
4. **Every message starts with an emoji and a bold word saying what kind of moment it is** (table
   below), so the chat list preview alone tells what happened.
5. **The last message leads with the outcome.** Verification and report merge into this one
   message for a run that ends after a check; it says the outcome, the cause in one line, what ran,
   what is left for the person (the report's follow-ups, at most 3), and a link to the incident on
   the dashboard when a dashboard address is set.
6. **A repeat says so.** An incident on a subject that had an incident end within the last 24 h
   opens with 🔁 `Again: <short name> · <time> after <earlier short name>` instead of 🔴, and its
   opener is a reply to the earlier incident's last message, so the quote shows how that one ended.
   Its later messages reply to its own opener as usual.
7. **A reply to any message of an incident is an instruction** for the plan that waits for a
   decision on it, as a reply to the proposal is today. When no plan waits, the existing answer
   ("Nothing is waiting for a decision on that incident") stays.

### Emoji per kind of message

| Emoji and word | When | Replaces |
|---|---|---|
| 🔴 **New** | an incident opens (also a host incident that waits for its host) | `incident_opened`, `incident_waiting` |
| 🔁 **Again** | an incident opens on a subject whose last incident ended < 24 h ago | the same |
| 🙋 **Your call** | a plan waits for a yes (Approve / Reject / It's expected buttons as today) | `proposal` |
| 🛑 **Plan refused** | Bleep's own checks refused its plan; reply or Let it go | `refused` |
| ⏰ **Still waiting** | reminder for an unanswered plan | `reminder`, `refused_reminder` |
| ℹ️ (no word) | a note in the middle of a run (planning again, waiting for the host) | `notice` |
| ❌ **Didn't hold** | the fix ran but the check failed; a new plan or an end follows | `verification_failed` |
| ✅ **Fixed** | end: `resolved` | `verification_ok` + `report` |
| ✅ **Healthy again** | end: healthy, but no step of the plan succeeded | `report` |
| ✅ **Back** | end: a waiting host answers again, nothing changed | `notice` (back) |
| ⚠️ **Needs you** | end: `no_action`; it says how the person can fix it | `report` |
| ❔ **Not verified** | end: `unverified` | `report` |
| 🙅 **Left to you** | end: `rejected` (No, or "Let it go") | `report` |
| 🛑 **Stopped** | end: `budget`, `replan_limit` | `report` |
| 🤫 **Quiet** / 👍 **Expected** | end: closed because the service was made quiet / you said it is expected | `notice` (closed) |

Exactly one message per incident leads with an end emoji, and it is the last one.

## Example chats

Rendered as the person sees them; `↪ [quote]` is Telegram's reply quote of the message named.

### db-down (fixed after a yes), interleaved with a host that stops answering

```
🔴 New: M1 #3 · host
M1 does not answer over SSH. Nothing to look at until it does; Bleep waits and changes nothing.

🔴 New: shop #14 · laptop
shop-web died. Looking into it, nothing changes without your yes.

↪ [🔴 New: shop #14 · laptop]
🙋 Your call
Likely cause (dependency, high): shop-db is stopped, so shop-web cannot reach its database.
Plan:
1. docker.start shop-db on laptop
2. docker.restart shop-web on laptop
Why: shop-web needs its database; starting it and restarting shop-web restores the shop.
[Approve] [Reject]

↪ [🔴 New: M1 #3 · host]
✅ Back after 6 min. M1 answers again; nothing was changed.

↪ [🔴 New: shop #14 · laptop]
✅ Fixed in 4 min
Cause: shop-db was stopped.
Ran: started shop-db, restarted shop-web. Checked: both running, the shop answers.
Details: https://<dashboard>/incidents/<id>
```

### bad-env (no action) and its repeat

```
🔴 New: shop #15 · laptop
shop-web keeps restarting. Looking into it, nothing changes without your yes.

↪ [🔴 New: shop #15 · laptop]
⚠️ Needs you · no action
Cause: DATABASE_URL points at host db; the shop's docs give shop-db:5432.
Next:
- Set DATABASE_URL to the documented shop-db:5432 where the shop is started
- Run docker compose up -d shop-web
Details: https://<dashboard>/incidents/<id>

↪ [⚠️ Needs you · no action]
🔁 Again: shop #16 · 1 h after shop #15
shop-web keeps restarting. Looking into it, nothing changes without your yes.

↪ [🔁 Again: shop #16 · 1 h after shop #15]
⚠️ Needs you · no action
Cause: DATABASE_URL still points at host db.
Next:
- Set DATABASE_URL to shop-db:5432 and run docker compose up -d shop-web
```

### A plan turned down by a reply

```
🔴 New: shop #17 · laptop
shop-worker was killed: out of memory. Looking into it, nothing changes without your yes.

↪ [🔴 New: shop #17 · laptop]
🙋 Your call
Likely cause (resource, medium): WORKER_BATCH_MB is 256; the docs give 8.
Plan:
1. docker.restart shop-worker on laptop
[Approve] [Reject]

↪ [🔴 New: shop #17 · laptop]
⏰ Still waiting for you · 30 min
Plan: 1. docker.restart shop-worker on laptop
[Approve] [Reject]

(you, replying to any of these) Don't restart, I'll change the batch size myself

↪ [🔴 New: shop #17 · laptop]
🙅 Left to you
Nothing was changed. Next: set WORKER_BATCH_MB=8 and restart shop-worker.
```

(The instruction makes Bleep plan again, as today; the end shown assumes the new plan is "no
action" and the run ends there. Which end appears depends on the outcome, per the table.)

## Constraints

- Templates stay plain `.md` files under `prompts/notify/`, loaded through `prompts.load`, escaped
  for MarkdownV2 by `notify/format.py`; bold is the only new markup and is added by the formatter,
  not written into templates as raw MarkdownV2.
- Plans are still shown in full (every step's action, target, host); the existing rules for plans
  that do not fit (Reject only) and for `coolify.set_env` values are unchanged.
- A Telegram outage still never breaks a run.
- Values are still redacted and capped; the short name is built from the subject's display name and
  goes through the same quoting as titles (#311).

## Calls made while writing this (to check)

- **Button labels stay as today** (Approve / Reject / It's expected / Let it go); the mockups showed
  the dashboard's wording. Aligning them is a one-line change if wanted.
- **The running number counts all incidents of that subject**, kept on the incident when it opens,
  so it never changes afterwards. The API returns it; showing it on the dashboard is a follow-up
  issue (the dashboard is out of scope here).
- **"Again" means: same subject, its last incident ended less than 24 h ago, any kind.** The 24 h is
  the longest repeat hold (#356). The wording says "after", not "same cause", because the kind may
  differ (bad-env's restart loop, then a single death).
- **The dashboard link needs an address Bleep does not know today**: a new optional setting
  (`DASHBOARD_URL`); with none set, the end message has no link. A `localhost` address works only on
  the machine running it, which is the person's choice.
- **The summary and the "Decision" line leave the Telegram report**; the thread already shows the
  decision, and the dashboard keeps the full report.

## Rules settled after a builder read

- **Folded repeats keep today's hold (#258): no extra opener.** A folded incident (`fold_of` set)
  sends no "opened" message today; its first real message (proposal, refused plan or end) becomes
  its opener, carries the 🔁 Again header and is not a reply to its own incident (it replies to the
  earlier incident's last message, as an Again opener does). The title's "(seen N times since …)"
  suffix is replaced by the Again line in Telegram. Reason: the anti-goal "no more messages than
  today".
- **Again = folded, or the subject's last incident ended less than 24 h ago.** Folded incidents are
  always Again. The quote goes to the subject's most recently closed incident's last message (not the
  fold group's first incident). The 24 h is fixed, not tied to `fold_window_h`.
- **Replies keep the stale-plan guard.** A reply to a proposal, refused-plan or reminder message stays
  bound to that message's plan, exactly as today (`answer_stale` for a replaced plan). Only a reply to
  a message without a plan (opener, notice, Didn't hold) binds to the plan that waits now, if any. A
  reply to a message of an earlier, closed incident stays with that incident ("Nothing is waiting").
- **The closes the table left out:** a run that stops on an error ends with ❗ **Stopped on an
  error**; an incident closed because its host was removed ends with ➖ **Closed: host removed**; a
  quiet close of a paused or waiting run ends with 🤫 **Quiet**. Each of these existing notices *is*
  the end message (no second one). The watchdog only sends ℹ️ notes and never ends an incident.
- **A missing opener.** If the opener never reached Telegram (Telegram was down, or the incident
  opened before this change), the first later message that does reach it gets the 🔴/🔁 header, is
  not a reply, and becomes the opener the rest reply to. An opener deleted in Telegram is covered by
  `allow_sending_without_reply`.
- **The short name is per service, as the dashboard groups them** (a Compose project is one service: shop-web, shop-worker and shop-db are all `shop #N`; the opener's line names the container). **A host's short name uses the hosts file's `id`** (`M1 #3`, `laptop #2`), not its long display
  name; the subject's display name is stored on the incident when it opens, so a later rename does
  not change old names.

## Left to the builder

How the opener's and the last message's ids are stored (`telegram_messages` already maps message ids
to incidents), the migration for the running number, the exact one-line wording of each template,
and how each closing path (report, host back, quiet, expected, watchdog) is routed so that exactly
one end message is sent.

## Done when (from #380)

- Every incident's first and last Telegram message are recognisable as start and end without reading
  the body; the last one leads with the outcome.
- A person can tell from the messages alone that two incidents are on the same service, and that one
  repeats an earlier one.
- Tests in `backend/tests/notify/` cover a fixed incident, a no-action incident, a repeat, a rejected
  plan, a host that comes back, a reply to a non-proposal message as an instruction, and a missing
  opener; every changed template still loads through `prompts.load`.
- `docs/design/notify.md` "Choices" gets a bullet per decision above, with #380.
