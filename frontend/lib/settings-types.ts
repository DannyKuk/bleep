// The settings page's contract: `GET/PUT /api/settings` (schemas/settings.py), `GET/PUT
// /api/plugins` (schemas/plugins.py) and `GET /api/telegram/status` (schemas/telegram.py).
// Written by hand like `api-types.ts`; no type here ever holds a secret's value.

import type { HostKind } from "@/lib/api-types";

/** A secret Bleep reads: the variable or the file it comes from, and whether it is there. */
export interface SecretStatus {
  env: string | null;
  file: string | null;
  set: boolean;
}

export interface UrlCheck {
  url: string;
  expect_status: number;
  body_contains: string | null;
}

export interface ServiceSettingsView {
  urls: UrlCheck[];
  rules: string[];
  /** The hosts file's and the dashboard's. */
  protected_containers: string[];
  /** Those the dashboard can never unprotect. */
  hosts_file_protected_containers: string[];
  /** Jobs meant to finish: the dashboard's list, else the hosts file's. */
  exits: string[];
}

/**
 * How Bleep reaches a host. `source` says who decides it: the hosts file (read-only here) or the
 * dashboard. A dashboard host's key and confirmed host key are only set or not, never content.
 */
export interface HostConnection {
  source: "file" | "dashboard";
  kind: HostKind;
  address: string | null;
  user: string | null;
  port: number | null;
  docker_socket: string | null;
  endpoint: string | null;
  key_file: SecretStatus | null;
  known_hosts_file: string | null;
  /** A dashboard host's private key. */
  ssh_key: SecretStatus | null;
  /** A dashboard host's confirmed host key. */
  host_key: SecretStatus | null;
  /** Of a dashboard host; sent back in `If-Match` by a change or a removal. */
  version: string | null;
}

export interface HostSettingsView {
  id: string;
  name: string;
  connection: HostConnection;
  rules: string[];
  /** In force: the dashboard's choice, else the hosts file's. */
  may_sleep: boolean;
  /** Keyed by the service's key on its host (`compose:shop`), not its id. */
  services: Record<string, ServiceSettingsView>;
}

export interface ModelChoice {
  provider: "openrouter" | "local";
  model: string;
  cheap_model: string;
  local_model: string;
}

export type EmbeddingsProvider = "local" | "openai";
export type TracingKind = "off" | "langfuse" | "langsmith";

/** The embedder in force: `model` is the endpoint's, `min_score` null the model's own cut-off. */
export interface EmbeddingsChoice {
  provider: EmbeddingsProvider;
  model: string | null;
  min_score: number | null;
}

/** The tracer in force, without its keys. An address the env decides is only its origin. */
export interface TracingChoice {
  tracing: TracingKind;
  langfuse_base_url: string;
  langfuse_ui_url: string;
  langfuse_project_id: string | null;
  langsmith_endpoint: string | null;
  langsmith_project: string;
}

export interface Thresholds {
  container_grace_s: number;
  repeated_deaths: number;
  repeated_window_s: number;
  url_failures_to_open: number;
  url_slow_ms: number;
  disk_percent: number;
  memory_available_percent_min: number;
  memory_polls: number;
  load_per_cpu: number;
  load_polls: number;
  host_unreachable_s: number;
  debounce_s: number;
  fold_window_h: number;
}

export interface ServiceOverrides {
  urls?: UrlCheck[];
  rules?: string[];
  protected_containers?: string[];
  /** Jobs meant to finish; replaces the hosts file's list. */
  exits?: string[];
}

export interface HostOverrides {
  rules?: string[];
  may_sleep?: boolean;
  services?: Record<string, ServiceOverrides>;
}

/** What the database changed, section by section; a field left out shows the layer below. */
export interface SettingsOverrides {
  thresholds?: Partial<Thresholds>;
  general?: GeneralOverrides;
  hosts?: Record<string, HostOverrides>;
  models?: Partial<ModelChoice>;
  embeddings?: Partial<EmbeddingsChoice>;
  tracing?: Partial<TracingChoice>;
  [section: string]: unknown; // budgets, which the dev panel edits
}

export interface SettingsView {
  models: ModelChoice;
  /** The OpenRouter key's variable; a local model needs none. */
  model_key: SecretStatus;
  budgets: Record<string, number>;
  thresholds: Thresholds;
  hosts: HostSettingsView[];
  plugins: Record<string, boolean>;
  /** The IANA zone times are shown in: the one chosen here, else BLEEP_TIMEZONE; null for none. */
  timezone: string | null;
  /** The language Bleep writes for the person in: the one chosen here, else English. */
  language: Language;
  embeddings: EmbeddingsChoice;
  tracing: TracingChoice;
  /** Every `section.field` the env decides (`models.provider`): it wins, and cannot be changed. */
  from_env: string[];
  /** Sections saved here that are in force only after a restart: `embeddings`, `tracing`. */
  restart_needed: string[];
  overrides: SettingsOverrides;
  /** Of `overrides`; a `PUT /api/settings` built from this view sends it back (`putSettings`). */
  version: string;
}

export type Language = "en" | "de";

/** One section: a save replaces it whole, so every field kept is sent again (`withGeneral`). */
export interface GeneralOverrides {
  timezone?: string;
  language?: Language;
}

/** The sections of `PUT /api/settings` this page writes. Each one given replaces its overrides. */
export interface SettingsUpdate {
  thresholds?: Partial<Thresholds>;
  general?: GeneralOverrides;
  models?: Partial<ModelChoice>;
  embeddings?: Partial<EmbeddingsChoice>;
  tracing?: Partial<TracingChoice>;
  hosts?: Record<string, HostOverrides>;
}

/** The part of a JSON schema the plugin settings form reads. */
export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  /** A string field's cap: the input stops there instead of the API refusing the save. */
  maxLength?: number;
  /** An example of a good value, shown as the empty input's placeholder when there is no default. */
  examples?: string[];
  /** Set on a `SecretStr`, which never gets an input. */
  writeOnly?: boolean;
  format?: string;
  anyOf?: JsonSchemaProperty[];
  items?: JsonSchemaProperty;
}

export interface PluginSettingsSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export interface PluginView {
  name: string;
  title: string;
  /** On and running: its tools and actions are in use. */
  enabled: boolean;
  /** The person's toggle as stored. On while `enabled` is off, the plugin is waiting: for a
   * required secret the env does not set, or for its code, which failed to build. */
  switched_on: boolean;
  settings: Record<string, unknown>;
  settings_schema: PluginSettingsSchema;
  /** `required: false` for one the plugin uses only when it is set (the MCP plugin's token). */
  secrets: { env: string; set: boolean; required: boolean }[];
  health: { ok: boolean; detail: string } | null;
  /** The plugin's own; a `PUT /api/plugins/{name}` built from this view sends it back. */
  version: string;
}

export interface PluginUpdate {
  enabled?: boolean;
  settings?: Record<string, unknown>;
}

export interface TelegramStatus {
  configured: boolean;
  /** Masked to its last two characters at most. */
  chat_id: string | null;
  last_poll: string | null;
  token: SecretStatus;
}
