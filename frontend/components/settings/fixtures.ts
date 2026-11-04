import { vi } from "vitest";

import type { SecretView } from "@/lib/secrets-api";
import type {
  HostSettingsView,
  PluginView,
  SettingsView,
  TelegramStatus,
  Thresholds,
} from "@/lib/settings-types";

export const THRESHOLD_VALUES: Thresholds = {
  container_grace_s: 60,
  repeated_deaths: 3,
  repeated_window_s: 600,
  url_failures_to_open: 2,
  url_slow_ms: 5000,
  disk_percent: 90,
  memory_available_percent_min: 5,
  memory_polls: 3,
  load_per_cpu: 2,
  load_polls: 3,
  host_unreachable_s: 300,
  debounce_s: 30,
};

export function sshHost(overrides: Partial<HostSettingsView> = {}): HostSettingsView {
  return {
    id: "m1",
    name: "M1",
    connection: {
      kind: "ssh",
      address: "m1.local",
      user: "danny",
      port: 22,
      docker_socket: "/var/run/docker.sock",
      endpoint: null,
      key_file: { env: null, file: "/keys/id_ed25519", set: false },
      known_hosts_file: "/keys/known_hosts",
    },
    rules: ["Ask before touching anything at night."],
    services: {
      "compose:shop": {
        urls: [
          { url: "https://shop.example.test/health", expect_status: 200, body_contains: null },
        ],
        rules: ["The db is shared with billing."],
        protected_containers: ["shop-db-1", "shop-cache-1"],
        hosts_file_protected_containers: ["shop-db-1"],
      },
    },
    ...overrides,
  };
}

export function settingsView(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    models: {
      provider: "openrouter",
      model: "openai/gpt-6-luna",
      cheap_model: "openai/gpt-6-luna",
      local_model: "openai/gpt-oss-20b",
    },
    model_key: { env: "OPENROUTER_API_KEY", file: null, set: true },
    budgets: { investigate_tool_calls: 8 },
    thresholds: THRESHOLD_VALUES,
    hosts: [sshHost()],
    plugins: { coolify: false },
    overrides: {
      hosts: {
        m1: { services: { "compose:shop": { protected_containers: ["shop-cache-1"] } } },
      },
    },
    version: "v1",
    ...overrides,
  };
}

export function plugin(overrides: Partial<PluginView> = {}): PluginView {
  return {
    name: "coolify",
    title: "Coolify",
    enabled: false,
    switched_on: false,
    settings: {},
    settings_schema: {
      properties: {
        deploy_timeout_s: { type: "integer", title: "Deploy Timeout S", default: 900 },
      },
    },
    secrets: [
      { env: "COOLIFY_URL", set: false, required: true },
      { env: "COOLIFY_TOKEN", set: false, required: true },
    ],
    health: null,
    version: "p1",
    ...overrides,
  };
}

export function secretView(name: string, overrides: Partial<SecretView> = {}): SecretView {
  return {
    name,
    kind: "secret",
    set: false,
    source: null,
    env_owned: false,
    carries: null,
    value: null,
    version: null,
    invalid: false,
    ...overrides,
  };
}

/** `GET /api/secrets` for a Bleep with nothing set. */
export function secretViews(): SecretView[] {
  return [
    secretView("OPENROUTER_API_KEY", { set: true, source: "env", env_owned: true }),
    secretView("TELEGRAM_BOT_TOKEN"),
    secretView("COOLIFY_URL", { kind: "address", carries: "COOLIFY_TOKEN" }),
    secretView("COOLIFY_TOKEN"),
  ];
}

export function telegram(overrides: Partial<TelegramStatus> = {}): TelegramStatus {
  return {
    configured: true,
    chat_id: "••••90",
    last_poll: "2026-11-03T10:00:00Z",
    token: { env: "TELEGRAM_BOT_TOKEN", file: null, set: true },
    ...overrides,
  };
}

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** pydantic's 422 body, as `parse_body` returns it: where and why, never the value sent. */
export function invalid(loc: (string | number)[], msg: string) {
  return json({ detail: [{ type: "value_error", loc, msg }] }, 422);
}

/** Answers each request by method and path from `routes`; a PUT answers from its queue. */
export function stubBackend(routes: Record<string, Response | Response[]>) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const reply = routes[key];
    if (Array.isArray(reply)) return reply.shift() ?? json({}, 500);
    if (reply) return reply.clone();
    return json({ detail: `no stub for ${key}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The parsed JSON body of the `n`th call (from 0) that used `method`. */
export function sentBody(fetchMock: ReturnType<typeof stubBackend>, method: string, n = 0) {
  const calls = fetchMock.mock.calls.filter(([, init]) => init?.method === method);
  return JSON.parse(String(calls[n][1]?.body));
}
