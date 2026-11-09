# Talk prompts

How Bleep reads what a person writes in its chat, and the fixed lines it answers with
(`talk/conversation.py`, docs/design/notify.md, "Talking to Bleep").

- `read.md` is the system prompt and `message.md` the message the model reads: the snapshot, the
  incident the message is about, the last turns and the person's text, each wrapped as untrusted
  data by the code that fills it.
- Every other file is a fixed line, sent as plain text: `refused_change`, `refused_secret`,
  `unclear`, `failed`, and `not_yet` with an `understood_*` line for the action Bleep read
  (`unknown_subject` and `unknown_incident` stand in for a name Bleep does not know). Only a
  question's answer is the model's own text.
- `unclear` offers only what this part can do, answers; when the confirmed actions land (#402) it
  offers snooze and watch again again. `spent` answers once chat's own daily cap is spent, and
  `link_removed` stands in for a link taken out of an answer.
- None of these can be overridden from the dev panel: they read the person's words and refuse
  them.
