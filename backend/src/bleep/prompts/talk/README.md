# Talk prompts

How Bleep reads what a person writes in its chat, and the fixed lines it answers with
(`talk/conversation.py`, `talk/offers.py`, docs/design/notify.md, "Talking to Bleep").

- `read.md` is the system prompt and `message.md` the message the model reads: the snapshot, the
  incident the message is about, the last turns and the person's text, each wrapped as untrusted
  data by the code that fills it. They are the only files here a model reads.
- Every other file is a fixed line, sent as plain text: `refused_change`, `refused_secret`,
  `unclear`, `failed`, `spent`. Only a question's answer is the model's own text.
- `confirm_*` are the lines an offer is confirmed under, one per action (#402), filled only with
  names and times from Bleep's own state, never with the model's words. The lines for an action
  that fails a check say why: `no_subject`, `ambiguous_subject`, `not_quiet`,
  `quiet_through_host`, `snooze_how_long`, `snooze_limit`, `no_incident`, `which_incident`,
  `incident_ended`, `no_plan`. `subject_on_host` names a service with its host when another
  service or a host has its name.
- `look_*` answer a look on request (#403, `talk/looks.py`), filled only with names from Bleep's
  state: why none is offered or opened (`look_host`, `look_open`, `look_snoozed`,
  `look_left_alone`, `look_quiet_host`, `look_unreachable`, `look_spent`, `look_unknown`), and
  `look_started` once a press opened the incident. `confirm_look_again` is its confirm line, and
  says that a look costs a model run.
- `plan_on_page` answers a yes to a waiting plan in the dashboard's panel, which points to the
  incident's page instead of showing the plan's buttons, and `plan_refused` one to a plan
  Bleep's own checks refused, which cannot be approved (#391). `busy` tells the panel that
  three messages are being answered already.
- A press of an offer's button is answered with the lines a Telegram button gets
  (`notify/answer_*`), on both surfaces (`talk/actions.py`).
- `when_*` are the words of a time in a chat (`talk/times.py`), shared with Telegram's messages.
- `link_removed` stands in for a link taken out of an answer.
- `log_*` build the Markdown transcript of the talk log (#411, `talk/log.py`), which the person
  exports to review answers: the heading (`log_head`), a heading per surface (`log_panel`,
  `log_telegram`, `log_empty`), each message (`log_exchange`, `log_about`) and the lines under
  it (`log_reading`, `log_unread`, `log_offer`, `log_refused`, `log_links`, `log_press`,
  `log_usage`, `log_cost_unknown`, `log_no_call`). No model reads them.
- None of these can be overridden from the dev panel: they read the person's words and refuse
  them.
