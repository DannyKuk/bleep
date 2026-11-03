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
}

/** How Bleep reaches a host, from the hosts file; the dashboard never changes it. */
export interface HostConnection {
  kind: HostKind;
  address: string | null;
  user: string | null;
  port: number | null;
  docker_socket: string | null;
  endpoint: string | null;
  key_file: SecretStatus | null;
  known_hosts_file: string | null;
}

export interface HostSettingsView {
  id: string;
  name: string;
  connection: HostConnection;
  rules: string[];
  /** Keyed by the service's key on its host (`compose:shop`), not its id. */
  services: Record<string, ServiceSettingsView>;
}

export interface ModelChoice {
  provider: "openrouter" | "local";
  model: string;
  cheap_model: string;
  local_model: string;
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
}

export interface ServiceOverrides {
  urls?: UrlCheck[];
  rules?: string[];
  protected_containers?: string[];
}

export interface HostOverrides {
  rules?: string[];
  services?: Record<string, ServiceOverrides>;
}

/** What the database changed, section by section; a field left out shows the layer below. */
export interface SettingsOverrides {
  thresholds?: Partial<Thresholds>;
  hosts?: Record<string, HostOverrides>;
  [section: string]: unknown; // models and budgets, which the dev panel edits
}

export interface SettingsView {
  models: ModelChoice;
  /** The OpenRouter key's variable; a local model needs none. */
  model_key: SecretStatus;
  budgets: Record<string, number>;
  thresholds: Thresholds;
  hosts: HostSettingsView[];
  plugins: Record<string, boolean>;
  overrides: SettingsOverrides;
}

/** The sections of `PUT /api/settings` this page writes. Each one given replaces its overrides. */
export interface SettingsUpdate {
  thresholds?: Partial<Thresholds>;
  hosts?: Record<string, HostOverrides>;
}

/** The part of a JSON schema the plugin settings form reads. */
export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
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
  enabled: boolean;
  settings: Record<string, unknown>;
  settings_schema: PluginSettingsSchema;
  secrets: { env: string; set: boolean }[];
  health: { ok: boolean; detail: string } | null;
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
