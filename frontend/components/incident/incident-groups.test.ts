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

describe("incident groups", () => {
  it("puts every status in exactly one group", () => {
    const grouped = groupIncidents(ALL.map((status) => incident({ id: status, status })));
    const ids = grouped.flatMap((g) => g.incidents.map((i) => i.id));
    expect(ids.sort()).toEqual([...ALL].sort());
    expect(grouped.map((g) => g.group)).toEqual(["needs_you", "on_it", "earlier"]);
    expect(groupOf("awaiting_approval")).toBe("needs_you");
    expect(groupOf("failed")).toBe("earlier");
  });

  it("matches each status with exactly the filters that name it", () => {
    for (const status of ALL) {
      const hits = FILTERS.filter((f) => f.id !== "all" && matchesFilter(status, f.id));
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
    expect(headline([])).toBe("No incidents yet.");
    expect(headline([incident({ status: "resolved" })])).toBe("Nothing needs you right now.");
    expect(headline([incident(), incident({ id: "b", status: "investigating" })])).toBe(
      "1 needs you, I am on 1.",
    );
  });
});
