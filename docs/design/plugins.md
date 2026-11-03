# Plugins

How Bleep takes on tools that share one connection, such as a Coolify instance (`backend/src/bleep/plugins/`, `api/plugins.py`). A plugin is switched on and off as a whole from the dashboard; its settings are stored with the other settings ([settings](settings.md)), its secrets never are (spec §4.8, §4.10).

| Piece | Path | What it does |
|---|---|---|
| **Plugin** | `plugins/base.py` | The base class: `name`, `title`, `settings_model` (non-secret), `secrets` (environment variable names), `tools(ctx)`, `act_handlers(ctx)` and `async health(ctx)`, each given a `PluginContext`. |
| **Registry** | `plugins/registry.py` | Which plugins are switched on, with what settings; their tools, act handlers and actions, read at every use; the view the API shows. |
| **Routes** | `api/plugins.py` | `GET /api/plugins` and `PUT /api/plugins/{name}` (`{enabled, settings}`, either part optional). |
| **List** | `wiring.py` (`PLUGINS`) | Every plugin Bleep has. |

## Choices

- **A plugin's methods take one `PluginContext`: its settings, its secrets by env name, `BLEEP_SECRET` and the guard log.** The plan had `tools(settings, secrets)` and `act_handlers()`. Reason: an act handler checks approval grants exactly as the Docker act tools do, so it needs the key the grants are signed with; one context keeps the three methods alike (#36).
- **A secret is the name of an environment variable, read when the plugin is switched on.** `GET /api/plugins` shows each one as `{env, set}`, never its value; the plugin's tools get it as a `SecretStr`, whose repr hides it. Reason: secrets never live in the database or reach the API or the model (§4.10) (#36).
- **A plugin's settings model must forbid unknown fields and may not have a field named like a secret** (`token`, `api_key`, `Password`; the [settings](settings.md) check), or the registry refuses the plugin when Bleep starts. Reason: settings are stored and shown, so a plugin author cannot put a token there by mistake (#36).
- **Every plugin is off until a person switches it on, and switching on needs valid settings and every secret set**; the refusal names the missing variable. Reason: a half-configured plugin would offer tools that fail on every call (#36).
- **The plugin's tools, act handlers and actions are read from the registry every time they are used.** `ToolRegistry` reads the switched-on plugins' tools on every read, `SettingsStore.enabled_actions` adds their actions to the core Docker ones, and Execute's act tools are a live view over the core ones and the plugins'. Reason: switching a plugin off takes its tools out of the next Investigate round, makes its actions fail the plan check as `disabled_action`, and leaves Execute without a handler even for a plan approved while it was on (#36).
- **A plugin's tool names start with `<name>_` and its actions with `<name>.`, and each action must be one Bleep knows (`schemas.graph.Action`).** Reason: a plugin can neither shadow a core tool nor add a Docker action that would skip the Docker act tools' checks (#36).
- **A plugin whose secret is unset at boot starts off, and its stored toggle stays on.** Reason: Bleep must start without it, and the plugin is on again once the variable is back, without a person switching it again (#36).
- **A change that cannot be saved puts the plugin back exactly as it was.** Settings and the toggle are applied in memory only after they validate and the plugin builds, then stored; a failed write restores the snapshot. Reason: the dashboard must never show a plugin as off while its tools are still offered, or the other way round (#36).
- **The health check is bounded to 5 s, and its text is redacted and has the plugin's own secret values replaced.** A check that raises or times out reads "the health check failed" or "timed out". Reason: a slow or broken plugin must not hang or break the plugins page, and a health message that quotes the token it used must not show it (#36).
