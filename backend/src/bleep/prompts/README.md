# Prompts

Every prompt the model sees is a `.md` file in this folder, grouped by area
(`investigate/system.md`, `report/summary.md`, ...). There is no prompt text in Python.

Load one with `prompts.load("area/name", **variables)`.

- Variables are `{name}` placeholders. Loading fails with `PromptError` on a missing or unused variable.
- Literal braces (JSON examples) are written `{{` and `}}`. Anything else in braces fails to load.
- With the dev panel on, an override saved there stands in for a file; it must use exactly the file's placeholders, and resetting it brings the file back. Only the prompts named in `OVERRIDABLE` (`__init__.py`) can be overridden; a new file is not, until it is named there (`docs/design/dev-panel.md`).
- `untrusted/` holds the untrusted-data rule of each prompt that has a `{untrusted_rule}` placeholder; the code that loads that prompt fills it from here, so an override never drops it. `untrusted/diagnose_planted_in.md` is Diagnose's instruction to report where planted orders were found, filled into `{planted_in_rule}` the same way.
- `graph/parts/` holds the headings, labels and fallbacks the graph nodes fill their prompts' sections with, loaded through `graph/describe.part`; they cannot be overridden.
- `tools/` and `plugins/` hold each agent tool's description (`tools/<tool>.md`, `plugins/<tool>.md`) and the sentences a tool answers with instead of data (`reply/`). `tools` and `plugins` cannot import this package, so `wiring` hands the tool registry `prompts.load` and each tool loads its texts through its `ToolContext`. A tool that returns text from outside Bleep fills its description's `{untrusted_rule}` with `untrusted/tool_output.md`, the one rule on untrusted tool output (#624). None of them can be overridden.
- `tests/test_prompts.py` loads every file here and checks its placeholders.
