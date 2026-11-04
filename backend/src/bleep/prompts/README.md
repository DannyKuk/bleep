# Prompts

Every prompt the model sees is a `.md` file in this folder, grouped by area
(`investigate/system.md`, `report/summary.md`, ...). There is no prompt text in Python.

Load one with `prompts.load("area/name", **variables)`.

- Variables are `{name}` placeholders. Loading fails with `PromptError` on a missing or unused variable.
- Literal braces (JSON examples) are written `{{` and `}}`. Anything else in braces fails to load.
- With the dev panel on, an override saved there stands in for a file; it must use exactly the file's placeholders, and resetting it brings the file back. `evals/`, `notify/` and `graph/system` cannot be overridden (`docs/design/dev-panel.md`).
- Tool descriptions are the tool function's docstring; that is the only model-facing text outside this folder.
- `tests/test_prompts.py` loads every file here and checks its placeholders.
