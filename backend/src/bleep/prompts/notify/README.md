# Notify templates

The fixed text of the messages Bleep sends to Telegram. These are not model prompts: no model
writes or reads them. They live here because every piece of fixed text is a `.md` file loaded
through `prompts.load`.

- `notify/format.py` fills a template and escapes it for Telegram's MarkdownV2: the template's own
  text and every value are escaped, so a service name or a model's sentence can never open a
  formatting entity or break the message. Templates therefore hold plain text only, no markup.
- Variables are plain `{name}` placeholders, as everywhere else.
- `answer_*.md` are the short pop-ups shown when a button is tapped or an instruction is sent; they
  are sent as plain text, not escaped.
