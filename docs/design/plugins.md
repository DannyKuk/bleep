# Plugins

How Bleep takes on tools that share one connection, such as a Coolify instance (`backend/src/bleep/plugins/`, `api/plugins.py`). A plugin is switched on and off as a whole from the dashboard; its settings are stored with the other settings ([settings](settings.md)), its secrets never are (spec §4.8, §4.10).

| Piece | Path | What it does |
|---|---|---|
| **Plugin** | `plugins/base.py` | The base class: `name`, `title`, `settings_model` (non-secret), `secrets` (environment variable names), `tools(ctx)`, `act_handlers(ctx)` and `async health(ctx)`, each given a `PluginContext`. |
| **Registry** | `plugins/registry.py` | Which plugins are switched on, with what settings; their tools, act handlers and actions, read at every use; the view the API shows. |
| **Routes** | `api/plugins.py` | `GET /api/plugins` and `PUT /api/plugins/{name}` (`{enabled, settings}`, either part optional). |
| **List** | `wiring.py` (`PLUGINS`) | Every plugin Bleep has: Coolify. |
| **Coolify** | `plugins/coolify/` | Read tools `coolify_applications`, `coolify_deployments` and `coolify_env_keys` ([below](#coolify)); no actions. |

## Choices

- **A plugin's methods take one `PluginContext`: its settings, its secrets by env name, `BLEEP_SECRET` and the guard log.** The plan had `tools(settings, secrets)` and `act_handlers()`. Reason: an act handler checks approval grants exactly as the Docker act tools do, so it needs the key the grants are signed with; one context keeps the three methods alike (#36).
- **A secret is the name of an environment variable, read when the plugin is switched on.** `GET /api/plugins` shows each one as `{env, set}`, never its value; the plugin's tools get it as a `SecretStr`, whose repr hides it. Reason: secrets never live in the database or reach the API or the model (§4.10) (#36).
- **A plugin's settings model must forbid unknown fields and may not have a field named like a secret** (`token`, `api_key`, `Password`; the [settings](settings.md) check), or the registry refuses the plugin when Bleep starts. Reason: settings are stored and shown, so a plugin author cannot put a token there by mistake (#36).
- **Every plugin is off until a person switches it on, and switching on needs valid settings and every secret set**; the refusal names the missing variable. Reason: a half-configured plugin would offer tools that fail on every call (#36).
- **The plugin's tools, act handlers and actions are read from the registry every time they are used.** `ToolRegistry` reads the switched-on plugins' tools on every read, `SettingsStore.enabled_actions` adds their actions to the core Docker ones, and Execute's act tools are a live view over the core ones and the plugins'. Reason: switching a plugin off takes its tools out of the next Investigate round, makes its actions fail the plan check as `disabled_action`, and leaves Execute without a handler even for a plan approved while it was on (#36).
- **A plugin's name is lower-case letters and digits and never `docker`; its tool names start with `<name>_` and its actions with `<name>.`, and each action must be one Bleep knows (`schemas.graph.Action`).** A core tool keeps its name over a plugin's, and a core action its handler. Reason: a plugin can neither shadow a core tool nor add a Docker action that would skip the Docker act tools' checks, and with no `_` in a name one plugin's prefix is never inside another's (#36).
- **A plugin whose own code fails while it is switched on is refused with its error's type only** ("plugin coolify could not be switched on (RuntimeError)"), as a 422 from the API and a warning on boot. Reason: the error's text could quote a setting or a secret, and a plugin that broke in a new version must not stop Bleep from starting (#36).
- **A plugin whose secret is unset at boot starts off, and its stored toggle stays on, also through a change of its settings alone.** Reason: Bleep must start without it, and the plugin is on again once the variable is back, without a person switching it again (#36).
- **A change that cannot be saved puts the plugin back exactly as it was.** Settings and the toggle are applied in memory only after they validate and the plugin builds, then stored; a failed write restores the snapshot. Reason: the dashboard must never show a plugin as off while its tools are still offered, or the other way round (#36).
- **The health check is bounded to 5 s, and its text is redacted and has the plugin's own secret values replaced.** A check that raises or times out reads "the health check failed" or "timed out". Only the secret as it is gets replaced; a plugin must not put a transformed secret in its health text. Reason: a slow or broken plugin must not hang or break the plugins page, and a health message that quotes the token it used must not show it (#36).
- **`plugins` builds on `tools`' types (`ToolSpec`, `ActHandler`) and `guard`'s known actions; `tools` reads the plugins only through the `ToolSource` protocol and never imports them.** Reason: a plugin hands Bleep tool specs and act handlers, so it needs their shapes, while the dependency still points one way (#36).

## Coolify

Setting `base_url` (where Coolify answers, such as `http://10.0.0.5:8000`; the API is under `/api/v1`, and a URL with a user or password is refused); secret `COOLIFY_TOKEN`, a Coolify API token sent as `Authorization: Bearer`. The plugin reads only, so a token with the read right is enough. It has no act handler, so `coolify.redeploy` and `coolify.set_env` stay out of the plan check's enabled set even while it is on.

### Endpoints

Checked on 2026-11-04 against the OpenAPI spec of Coolify v4.6.0 (`openapi.yaml` at tag `v4.6.0` of coollabsio/coolify, the latest release, published 2026-11-02) and its route file (`routes/api.php`), then read on a real server.

| Use | Request | Token right |
|---|---|---|
| Health | `GET /api/v1/version` | read |
| `coolify_applications`, and finding an app by name | `GET /api/v1/applications` | read |
| `coolify_deployments` | `GET /api/v1/deployments/applications/{uuid}?take=5` | read |
| `coolify_env_keys` | `GET /api/v1/applications/{uuid}/envs` | read |

The two actions' endpoints were checked the same way; Bleep does not call them:

| Action | Request | Token right |
|---|---|---|
| `coolify.redeploy` | `POST /api/v1/deploy?uuid={uuid}&force=false` answers `{deployments: [{deployment_uuid, ...}]}`; `GET /api/v1/deployments/{deployment_uuid}` reads its status | deploy, read |
| `coolify.set_env` | `PATCH /api/v1/applications/{uuid}/envs` `{key, value}` updates a key and answers 404 for a key the application lacks; `POST` the same body creates one | write |

Two things the spec does not say: `GET /deploy` answers that POST is required (v4.6.0 routes it to `post_required`), and `GET /deployments/applications/{uuid}` is documented as a list of applications but answers `{count, deployments}`. A deployment's status is one of `queued`, `in_progress`, `finished`, `failed` and `cancelled-by-user` (`App\Enums\ApplicationDeploymentStatus`).

### The real server

Three GET requests to a production Coolify server on 2026-11-04 with a root token (after an unauthenticated `GET /api/health` to find the address), nothing written, and only the shapes kept (the fixtures in `backend/tests/fixtures/coolify/` are rewritten by hand: every id, name, domain, commit and value is made up):

- `GET /version`: 200, `text/html`, `4.6.0`.
- `GET /applications`: 200, a list of 3 applications with the spec's `Application` fields, among them `manual_webhook_secret_github`, `_gitlab`, `_bitbucket` and `_gitea` as strings, `http_basic_auth_password`, `custom_labels`, `docker_compose_raw` and `git_full_url`, and some the spec does not list (`last_online_at`, `restart_count`, `server_status`, `container_present`, `destination`); `status` reads `running:healthy`.
- `GET /deployments/applications/{uuid}?take=2`: 200, `{count: int, deployments: [...]}`; each deployment with `status` (`finished` on both), `commit`, `commit_message`, `created_at`, `finished_at` (not in the spec) and `logs`, which a root token is shown.

### Choices

- **Bleep reads only the fields it names (`schemas/coolify.py`), and never an env var's value.** The application list carries webhook secrets and an application's deployments their build logs; the models drop every other field when the answer is parsed, and `coolify_env_keys` parses keys and flags only. What is shown still goes through the guard like any tool output (redacted, cleaned, wrapped as untrusted, source `coolify`). Reason: the real server's answers hold secrets in fields the spec does not mark as such (#38).
- **A Coolify failure is one fixed sentence**, never Coolify's answer or httpx's message: "Coolify refused the token (401); check COOLIFY_TOKEN", "Coolify could not be reached (ConnectError)", "Coolify has no such application". A tool returns it as its result, the health check as its text. Redirects are not followed. Reason: an error text can echo the URL or a value, and the token must never be sent elsewhere (#38).
- **The read tools are offered on a host whose containers carry Coolify application labels**, and list the applications that run there first, marked "(on this host)" by the uuid or id their containers carry. An application is named to a tool by its uuid or its name; two of one name are refused with a request for the uuid. Reason: one Coolify can manage several servers, and a name is not unique (#38).
- **The health check reads `GET /version` with the token** and says "Coolify 4.6.0 answers with the token", or only "Coolify answers with the token" when the answer does not look like a version. Reason: it proves the address and the token at once, and an answer that is not a version is not repeated on the plugins page (#38).
- **Limits.** `coolify_deployments` leaves out the build logs, which a root token is shown; a failed build is seen by its status only. No eval covers these tools yet: the demo stack has no Coolify (#38).
