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
- `plan_on_page` answers a yes to a waiting plan in the dashboard's panel, which points to the
  incident's page instead of showing the plan's buttons (#391).
- A press of an offer's button is answered with the lines a Telegram button gets
  (`notify/answer_*`), on both surfaces (`talk/actions.py`).
- `not_yet` with `understood_look_again` (and `unknown_subject` for a name Bleep does not know)
  answers a look on request, which comes with #403.
- `when_*` are the words of a time in a chat (`talk/times.py`), shared with Telegram's messages.
- `link_removed` stands in for a link taken out of an answer.
- None of these can be overridden from the dev panel: they read the person's words and refuse
  them.
