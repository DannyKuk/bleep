import { describe, expect, it } from "vitest";

import type { Incident, IncidentStatus, Outcome } from "@/lib/api-types";
import {
  FAILED_NEEDS_YOU_MS,
  incidentMood,
  isQuietSubject,
  overallMood,
  overallState,
  reportMood,
  stateMood,
  type Mood,
  type OverallState,
} from "@/lib/mood";

const NOW = Date.parse("2026-11-04T12:00:00Z");

function incident(
  status: IncidentStatus,
  id: string = status,
  closedAt: string | null = null,
): Incident {
  return {
    id,
    subject: { kind: "service", host_id: "demo", service_id: "shop" },
    status,
    opened_at: "2026-11-04T11:00:00Z",
    closed_at: closedAt,
    title: "shop is down",
    cost_usd: null,
    tokens: 0,
    trace_url: null,
    prompt_overrides: null,
    fold_of: null,
    short_name: null,
  };
}

const SNOOZED = {
  scope: "host",
  mode: "leave_alone",
  until: null,
  since: "2026-11-04T10:00:00Z",
} as const;
const hostOf = (overrides: Partial<Parameters<typeof overallMood>[1][number]> = {}) => ({
  id: "demo",
  reachable: true,
  may_sleep: false,
  quiet: null,
  quiet_service_ids: [],
  ...overrides,
});
const reachable = [hostOf()];
const unreachable = [hostOf(), hostOf({ id: "other", reachable: false })];
const justNow = new Date(NOW - 60_000).toISOString();
const longAgo = new Date(NOW - FAILED_NEEDS_YOU_MS - 1).toISOString();

describe("incidentMood", () => {
  const cases: [IncidentStatus, boolean, Mood][] = [
    ["open", false, "working"],
    ["investigating", false, "working"],
    ["awaiting_approval", false, "needs_you"],
    ["awaiting_approval", true, "not_sure"],
    ["executing", false, "working"],
    ["verifying", false, "working"],
    ["paused", false, "working"],
    ["waiting", false, "working"],
    ["resolved", false, "all_good"],
    ["closed", false, "resting"],
    ["failed", false, "needs_you"],
    // unsure only changes the mood of a plan that waits for the human
    ["investigating", true, "working"],
    ["resolved", true, "all_good"],
    ["failed", true, "needs_you"],
  ];
  it.each(cases)("%s (unsure %s) is %s", (status, unsure, mood) => {
    expect(incidentMood(status, unsure)).toBe(mood);
  });

  it("is sure unless told otherwise", () => {
    expect(incidentMood("awaiting_approval")).toBe("needs_you");
  });
});

describe("reportMood", () => {
  const cases: [Outcome, Mood][] = [
    ["resolved", "all_good"],
    ["healthy", "all_good"],
    ["unverified", "needs_you"],
    ["budget", "needs_you"],
    ["rejected", "resting"],
    ["no_action", "resting"],
    ["replan_limit", "resting"],
  ];
  it.each(cases)("%s is %s", (outcome, mood) => {
    expect(reportMood(outcome)).toBe(mood);
  });
});

describe("overallState", () => {
  const single: [IncidentStatus, OverallState][] = [
    ["open", "amber"],
    ["investigating", "amber"],
    ["awaiting_approval", "red"],
    ["executing", "amber"],
    ["verifying", "amber"],
    ["paused", "amber"],
    ["resolved", "green"],
    ["closed", "green"],
  ];
  it.each(single)("one %s incident makes it %s", (status, state) => {
    expect(overallState([incident(status)], reachable, NOW)).toBe(state);
  });

  it("is green with nothing at all", () => {
    expect(overallState([], [], NOW)).toBe("green");
  });

  it("is red for a run that failed in the last day, green once that day is over", () => {
    expect(overallState([incident("failed", "f", justNow)], reachable, NOW)).toBe("red");
    expect(overallState([incident("failed", "f", longAgo)], reachable, NOW)).toBe("green");
  });

  it("is amber when a host cannot be reached, even with no incident", () => {
    expect(overallState([], unreachable, NOW)).toBe("amber");
  });

  it("takes the worst of several: red over amber over green", () => {
    const mixed = [incident("resolved"), incident("investigating"), incident("awaiting_approval")];
    expect(overallState(mixed, unreachable, NOW)).toBe("red");
    expect(overallState([incident("closed"), incident("verifying")], reachable, NOW)).toBe("amber");
  });
});

describe("overallMood", () => {
  it("is all good when nothing is open and every host answers", () => {
    expect(overallMood([incident("resolved")], reachable, new Set(), NOW)).toBe("all_good");
  });

  it("is working while Bleep is on an incident or a host is unreachable", () => {
    expect(overallMood([incident("executing")], reachable, new Set(), NOW)).toBe("working");
    expect(overallMood([], unreachable, new Set(), NOW)).toBe("working");
  });

  it("is not sure when the only plan waiting for the human rests on an unsure diagnosis", () => {
    const waiting = incident("awaiting_approval", "a");
    expect(overallMood([waiting], reachable, new Set(["a"]), NOW)).toBe("not_sure");
  });

  it("needs you over not sure, and not sure over working", () => {
    const sure = incident("awaiting_approval", "sure");
    const unsure = incident("awaiting_approval", "unsure");
    const working = incident("investigating", "w");
    expect(overallMood([unsure, sure], reachable, new Set(["unsure"]), NOW)).toBe("needs_you");
    expect(overallMood([working, unsure], unreachable, new Set(["unsure"]), NOW)).toBe("not_sure");
  });

  it("needs you for a recent failure, whatever else is unsure", () => {
    const failed = incident("failed", "f", justNow);
    const unsure = incident("awaiting_approval", "u");
    expect(overallMood([failed, unsure], reachable, new Set(["u"]), NOW)).toBe("needs_you");
  });

  it("agrees with overallState when no diagnosis is known", () => {
    const sets: Incident[][] = [
      [],
      [incident("open")],
      [incident("awaiting_approval")],
      [incident("failed", "f", justNow)],
    ];
    for (const incidents of sets) {
      const state = overallState(incidents, reachable, NOW);
      expect(overallMood(incidents, reachable, new Set(), NOW)).toBe(stateMood(state));
    }
  });
});

describe("quiet subjects", () => {
  const failed = incident("failed", "f", justNow);

  it("a failed incident of a quiet service does not make the mood needs_you", () => {
    const hosts = [hostOf({ quiet_service_ids: ["shop"] })];
    expect(overallMood([failed], hosts, new Set(), NOW)).toBe("all_good");
    expect(overallState([failed], hosts, NOW)).toBe("green");
  });

  it("a failed incident of a service quiet through its host does not either", () => {
    const hosts = [hostOf({ quiet: { ...SNOOZED } })];
    expect(overallMood([failed], hosts, new Set(), NOW)).toBe("all_good");
  });

  it("a failed incident of another service on the same host still does", () => {
    const hosts = [hostOf({ quiet_service_ids: ["blog"] })];
    expect(overallMood([failed], hosts, new Set(), NOW)).toBe("needs_you");
  });

  it("an unreachable quiet host does not make the mood working", () => {
    const hosts = [hostOf({ reachable: false, quiet: { ...SNOOZED } })];
    expect(overallMood([], hosts, new Set(), NOW)).toBe("all_good");
    expect(overallState([], hosts, NOW)).toBe("green");
  });

  it("an asleep host that may sleep does not make the mood working", () => {
    const hosts = [hostOf({ reachable: false, may_sleep: true })];
    expect(overallMood([], hosts, new Set(), NOW)).toBe("all_good");
    expect(overallState([], hosts, NOW)).toBe("green");
  });

  it("an incident waiting for its host counts as one Bleep is on", () => {
    expect(overallMood([incident("waiting")], [], new Set(), NOW)).toBe("working");
  });

  it("knows a quiet subject by its service or its host", () => {
    const service = { kind: "service", host_id: "demo", service_id: "shop" } as const;
    const host = { kind: "host", host_id: "demo", service_id: null } as const;
    const quietService = [hostOf({ quiet_service_ids: ["shop"] })];
    expect(isQuietSubject(service, quietService)).toBe(true);
    expect(isQuietSubject(host, quietService)).toBe(false);
    expect(isQuietSubject(host, [hostOf({ quiet: { ...SNOOZED } })])).toBe(true);
    expect(isQuietSubject(service, [])).toBe(false);
  });
});

describe("stateMood", () => {
  it.each([
    ["red", "needs_you"],
    ["amber", "working"],
    ["green", "all_good"],
  ] as const)("%s is %s", (state, mood) => {
    expect(stateMood(state)).toBe(mood);
  });
});
