# Shown texts

The fixed texts the other parts of the backend write for the person (#427): the notices on an
incident's timeline, the report's outcome, timeline and decision lines, "This is expected" and
its answers, and the words for a plan's targets.

- Code keeps such a text as `schemas.shown.Shown`: the template's name here (`notice/back`) and
  its values, or several parts joined. It is stored in English, `prompts.show(text, "en")`, in
  the same words as before #427; that English is what any model reads of it (a notice goes into
  the report's facts and the chat's snapshot) and what the dev panel and the talk log show.
- Next to the English, an event's payload keeps the shown text (`"shown"`), a report its
  `shown` fields and timeline entries; the API's views and the notifier render those in the
  person's language with `prompts.show`. A text stored before #427 has none and shows as stored.
- Every file has a German twin in `de/` with the same placeholders; `tests/test_prompts.py`
  fails on a file in one language only. None of them can be overridden from the dev panel.
- A value is plain text (a name, a number, English from elsewhere such as a refusal reason) or
  a shown text of its own, filled in the same language.
- `channel/on_*` and `channel/from_*` carry their preposition, since German needs other ones
  ("im Dashboard", "über Telegram").
