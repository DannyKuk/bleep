import { describe, expect, it } from "vitest";

import { formatSince } from "@/lib/format";

/** A local wall-clock time, as the ISO string the API sends. */
const local = (y: number, m: number, d: number, h: number, min: number) =>
  new Date(y, m - 1, d, h, min).toISOString();

describe("formatSince", () => {
  const now = new Date(2026, 10, 5, 16, 30); // Thu 5 Nov 2026, 16:30 local

  it("says only the time for today", () => {
    expect(formatSince(local(2026, 11, 5, 9, 5), now)).toBe("9:05");
  });

  it("says yesterday", () => {
    expect(formatSince(local(2026, 11, 4, 23, 40), now)).toBe("yesterday 23:40");
  });

  it("names the weekday within the last week", () => {
    expect(formatSince(local(2026, 11, 2, 8, 0), now)).toBe("Mon 8:00");
  });

  it("gives the date when it is older, and the year when it is another one", () => {
    expect(formatSince(local(2026, 10, 20, 14, 2), now)).toBe("20 Oct 14:02");
    expect(formatSince(local(2025, 12, 31, 14, 2), now)).toBe("31 Dec 2025 14:02");
  });
});
