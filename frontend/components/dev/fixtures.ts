import { vi } from "vitest";

import type { Budgets, DevSettingsView, ModelChoice, PromptView } from "@/lib/api-types";

export function json(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Answers each request with the next reply, in order. */
export function stubBackend(...replies: Response[]) {
  const fetchMock = vi.fn<typeof fetch>(async () => replies.shift() ?? json([]));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const sentBody = (fetchMock: ReturnType<typeof stubBackend>, call: number) =>
  JSON.parse(String(fetchMock.mock.calls[call][1]?.body)) as unknown;

export const MODELS: ModelChoice = {
  provider: "openrouter",
  model: "openai/gpt-6-luna",
  cheap_model: "openai/gpt-6-luna",
  local_model: "openai/gpt-oss-20b",
};

export const BUDGETS: Budgets = {
  investigate_tool_calls: 8,
  low_confidence: 0.6,
  verify_loops: 2,
  replans: 3,
  incident_tokens: 150_000,
  daily_cost_usd: 1,
  approval_reminder_min: 60,
  model_timeout_s: 120,
  paused_retry_min: 5,
  stuck_after_min: 30,
};

export const settingsView = (overrides: Partial<DevSettingsView> = {}): DevSettingsView => ({
  models: MODELS,
  budgets: BUDGETS,
  overrides: {},
  version: "v1",
  ...overrides,
});

export const prompt = (overrides: Partial<PromptView> = {}): PromptView => ({
  name: "watcher/recovery_line",
  placeholders: ["logs"],
  file_text: "Why did it stop?\n\n{logs}",
  override: null,
  overridden_at: null,
  version: "p1",
  ...overrides,
});
