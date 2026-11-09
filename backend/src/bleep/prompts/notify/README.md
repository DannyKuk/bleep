# Notify templates

The fixed text of the messages Bleep sends to Telegram. These are not model prompts: no model
writes or reads them. They live here because every piece of fixed text is a `.md` file loaded
through `prompts.load`.

- `notify/format.py` fills a template and escapes it for Telegram's MarkdownV2: the template's own
  text and every value are escaped, so a service name or a model's sentence can never open a
  formatting entity or break the message. Templates therefore hold plain text only, no markup.
  The first line of a message says what kind of moment it is (an emoji and a word); the notifier
  has `format.bold_first_line` make it bold, the only markup a message carries.
- Each incident is one thread: `head_new` or `head_again` and a line from `seen/` (what opened it)
  lead its first message, every later one replies to it, and an `end_*` template leads its last.
  The end message is assembled from `end_report`, `end_checked`, `end_next` and `end_details`, each
  only when it has something to say.
- `where_host`, `ran_nothing`, `duration_*`, `when_*` (`when_utc` adds the zone when none is
  configured) and `on_it_morning` are the few words that go into a message or a button as values
  (`format.phrase`), so a translation changes files only.
- Variables are plain `{name}` placeholders, as everywhere else.
- `answer_*.md` are the short pop-ups shown when a button is tapped or an instruction is sent; they
  are sent as plain text, not escaped.
