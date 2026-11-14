import type { UsageView } from "@/lib/api-types";

const NO_OUTCOMES = {
  resolved: 0,
  healthy: 0,
  rejected: 0,
  no_action: 0,
  unverified: 0,
  budget: 0,
  replan_limit: 0,
  recovered: 0,
};

/** An empty range: no call, no incident, nothing in either log. */
export const EMPTY: UsageView = {
  days: 30,
  through: "2026-11-13",
  totals: {
    cost_usd: 0,
    calls: 0,
    incident_calls: 0,
    chat_calls: 0,
    unpriced_calls: 0,
    local_calls: 0,
    unpriced_local_calls: 0,
    tokens_in: 0,
    tokens_out: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
  },
  models: [],
  per_day: [],
  incidents: { count: 0, average_cost_usd: null, most_expensive: null },
  endings: { outcomes: NO_OUTCOMES, failed: 0, open: 0, other: 0 },
  actions: [],
  guard: [],
  today: { spent_usd: 0, daily_cap_usd: 1, chat_spent_usd: 0, chat_daily_cap_usd: 0.2 },
};

/** A busy range: two paid models, a local one and a row from before the provider column. */
export const BUSY: UsageView = {
  days: 30,
  through: "2026-11-13",
  totals: {
    cost_usd: 1.5,
    calls: 24,
    incident_calls: 20,
    chat_calls: 4,
    unpriced_calls: 3,
    local_calls: 6,
    unpriced_local_calls: 0,
    tokens_in: 1_200_000,
    tokens_out: 34_000,
    cache_read_tokens: 300_000,
    cache_write_tokens: 40_000,
  },
  models: [
    {
      model: "claude-sonnet-5-5",
      provider: "anthropic",
      calls: 12,
      tokens_in: 900_000,
      tokens_out: 20_000,
      cache_read_tokens: 300_000,
      cache_write_tokens: 40_000,
      cost_usd: 1.2,
      unpriced_calls: 0,
    },
    {
      model: "openai/gpt-6-luna",
      provider: "openrouter",
      calls: 6,
      tokens_in: 250_000,
      tokens_out: 9_000,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0.3,
      unpriced_calls: 0,
    },
    {
      model: "qwen3:8b",
      provider: "local",
      calls: 6,
      tokens_in: 40_000,
      tokens_out: 4_000,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0,
      unpriced_calls: 0,
    },
    {
      model: "mystery/m",
      provider: "unknown",
      calls: 3,
      tokens_in: 10_000,
      tokens_out: 1_000,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0,
      unpriced_calls: 3,
    },
  ],
  per_day: [
    {
      day: "2026-11-11",
      model: "claude-sonnet-5-5",
      cost_usd: 0.7,
      incident_calls: 7,
      chat_calls: 0,
    },
    {
      day: "2026-11-12",
      model: "claude-sonnet-5-5",
      cost_usd: 0.5,
      incident_calls: 4,
      chat_calls: 1,
    },
    {
      day: "2026-11-12",
      model: "openai/gpt-6-luna",
      cost_usd: 0.3,
      incident_calls: 4,
      chat_calls: 2,
    },
    { day: "2026-11-13", model: "qwen3:8b", cost_usd: 0, incident_calls: 5, chat_calls: 1 },
    { day: "2026-11-10", model: "mystery/m", cost_usd: 0, incident_calls: 3, chat_calls: 0 },
  ],
  incidents: {
    count: 7,
    average_cost_usd: 0.25,
    most_expensive: {
      incident_id: "inc_shop",
      name: "shop-web died on a/shop",
      cost_usd: 0.8,
    },
  },
  endings: {
    outcomes: { ...NO_OUTCOMES, resolved: 3, healthy: 1, rejected: 1, recovered: 1 },
    failed: 1,
    open: 0,
    other: 0,
  },
  actions: [
    { action: "docker.restart", status: "done", count: 3 },
    { action: "docker.restart", status: "failed", count: 1 },
    { action: "coolify.redeploy", status: "skipped", count: 1 },
    { action: "plugin.new_thing", status: "done", count: 2 },
  ],
  guard: [
    { kind: "redacted", count: 12 },
    { kind: "refused_command", count: 2 },
    { kind: "future_kind", count: 1 },
  ],
  today: { spent_usd: 0.85, daily_cap_usd: 1, chat_spent_usd: 0.2, chat_daily_cap_usd: 0.2 },
};

/** A range with only local calls: nothing priced, nothing spent. */
export const LOCAL_ONLY: UsageView = {
  ...EMPTY,
  days: 7,
  totals: {
    ...EMPTY.totals,
    calls: 8,
    incident_calls: 6,
    chat_calls: 2,
    local_calls: 8,
    unpriced_local_calls: 8,
    tokens_in: 20_000,
    tokens_out: 2_000,
  },
  models: [
    {
      model: "qwen3:8b",
      provider: "local",
      calls: 8,
      tokens_in: 20_000,
      tokens_out: 2_000,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      cost_usd: 0,
      unpriced_calls: 8,
    },
  ],
  per_day: [
    { day: "2026-11-12", model: "qwen3:8b", cost_usd: 0, incident_calls: 6, chat_calls: 2 },
  ],
  incidents: { count: 2, average_cost_usd: null, most_expensive: null },
  endings: { outcomes: { ...NO_OUTCOMES, resolved: 1 }, failed: 0, open: 1, other: 0 },
};

/** The same view for another range. */
export const forRange = (view: UsageView, days: UsageView["days"]): UsageView => ({
  ...view,
  days,
});
