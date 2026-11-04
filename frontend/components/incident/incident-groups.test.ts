import { describe, expect, it } from "vitest";

import { incident } from "@/components/incident/fixtures";
import {
  FILTERS,
  formatDuration,
  groupIncidents,
  groupOf,
  headline,
  matchesFilter,
} from "@/components/incident/incident-groups";
import type { IncidentStatus } from "@/lib/api-types";

const ALL: IncidentStatus[] = [
  "open",
  "investigating",
  "awaiting_approval",
  "executing",
  "verifying",
  "paused",
  "resolved",
  "closed",
  "failed",
];

const NOW = Date.parse("2026-11-04T18:00:00Z");
const DAY = 24 * 60 * 60 * 1000;

describe("incident groups", () => {
  it("puts every status in exactly one group", () => {
    const grouped = groupIncidents(
      ALL.map((status) => incident({ id: status, status })),
      NOW,
    );
    const ids = grouped.flatMap((g) => g.incidents.map((i) => i.id));
    expect(ids.sort()).toEqual([...ALL].sort());
    expect(grouped.map((g) => g.group)).toEqual(["needs_you", "on_it", "earlier"]);
    expect(groupOf(incident(), NOW)).toBe("needs_you");
    expect(groupOf(incident({ status: "failed" }), NOW)).toBe("earlier");
  });

  it("matches each status with exactly the filters that name it", () => {
    for (const status of ALL) {
      const hits = FILTERS.filter(
        (f) => f.id !== "all" && matchesFilter(incident({ status }), f.id, NOW),
      );
      expect(hits, status).toHaveLength(1);
    }
  });

  it("writes the duration of a closed incident only", () => {
    expect(formatDuration("2026-11-02T18:00:00Z", null)).toBe("");
    expect(formatDuration("2026-11-02T18:00:00Z", "2026-11-02T18:04:00Z")).toBe("4 min");
    expect(formatDuration("2026-11-02T18:00:00Z", "2026-11-02T20:05:00Z")).toBe("2 h 5 min");
    expect(formatDuration("2026-11-02T18:00:00Z", "2026-11-02T18:00:10Z")).toBe("under 1 min");
  });

  it("counts what needs the human in the headline", () => {
    expect(headline([], NOW)).toBe("No incidents yet.");
    expect(headline([incident({ status: "resolved" })], NOW)).toBe("Nothing needs you right now.");
    expect(headline([incident(), incident({ id: "b", status: "investigating" })], NOW)).toBe(
      "1 needs you, I am on 1.",
    );
  });

  it("keeps a failed run under Needs you for a day after it ended, then under Earlier", () => {
    const endedAgo = (ms: number) =>
      incident({ status: "failed", closed_at: new Date(NOW - ms).toISOString() });
    expect(groupOf(endedAgo(DAY / 2), NOW)).toBe("needs_you");
    expect(groupOf(endedAgo(DAY + 60_000), NOW)).toBe("earlier");
    expect(matchesFilter(endedAgo(DAY / 2), "needs_you", NOW)).toBe(true);
    expect(matchesFilter(endedAgo(DAY / 2), "closed_or_failed", NOW)).toBe(true);
    expect(headline([endedAgo(DAY / 2)], NOW)).toBe("1 needs you.");
    expect(headline([endedAgo(DAY + 60_000)], NOW)).toBe("Nothing needs you right now.");
  });
});
