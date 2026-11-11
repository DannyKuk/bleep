# API error texts

The details the API answers errors with (#456), in the language picked in the settings.

- A route raises `api.errors.Refusal(status, "name", **values)` and `<name>.md` here is the text;
  an error another part raised carries its `template` and `values` beside its English text
  (`schemas.refusal.Templated`). The handlers in `api/errors.py` render it.
- `problem/<type>.md` is the message of a pydantic error of that type, filled with the error's
  `ctx` (`problem/less_than_equal.md`: `Input should be less than or equal to {le}`). A count of one
  has `<type>_one.md`. A type with no file keeps pydantic's English message.
- A value is plain text, a count (grouped by the language: `100,000`, `100.000`), a `shown/` text
  or another error's text, filled in the same language.
- Every file has a German twin in `de/` with the same placeholders. None of them can be
  overridden from the dev panel, and what an error stores or logs (`str(error)`) stays English.
