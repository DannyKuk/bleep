import { describe, expect, it } from "vitest";

import { formatCount, formatDateTime, formatSince, formatTime } from "@/lib/format";

/** A local wall-clock time, as the ISO string the API sends. */
const local = (y: number, m: number, d: number, h: number, min: number) =>
  new Date(y, m - 1, d, h, min).toISOString();

describe("formatSince", () => {
  const now = new Date(2026, 10, 5, 16, 30); // Thu 5 Nov 2026, 16:30 local

  it("says only the time for today", () => {
    expect(formatSince(local(2026, 11, 5, 9, 5), now, undefined, "en")).toBe("9:05");
  });

  it("says yesterday", () => {
    expect(formatSince(local(2026, 11, 4, 23, 40), now, undefined, "en")).toBe("yesterday 23:40");
  });

  it("names the weekday within the last week", () => {
    expect(formatSince(local(2026, 11, 2, 8, 0), now, undefined, "en")).toBe("Mon 8:00");
  });

  it("gives the date when it is older, and the year when it is another one", () => {
    expect(formatSince(local(2026, 10, 20, 14, 2), now, undefined, "en")).toBe("20 Oct 14:02");
    expect(formatSince(local(2025, 12, 31, 14, 2), now, undefined, "en")).toBe("31 Dec 2025 14:02");
  });

  it("says it in German for de", () => {
    expect(formatSince(local(2026, 11, 4, 23, 40), now, undefined, "de")).toBe("gestern 23:40");
    expect(formatSince(local(2026, 11, 2, 8, 0), now, undefined, "de")).toBe("Mo 8:00");
    expect(formatSince(local(2026, 10, 20, 14, 2), now, undefined, "de")).toBe("20. Okt. 14:02");
  });
});

describe("dates and counts in German", () => {
  it("writes a date, a time and a count the German way", () => {
    const at = "2026-11-02T18:01:05Z";
    expect(formatDateTime(at, "Europe/Vienna", "de")).toBe("02.11.2026, 19:01");
    expect(formatTime(at, "Europe/Vienna", "de")).toBe("19:01:05");
    expect(formatCount(5400, "de")).toBe("5.400");
    expect(formatCount(5400, "en")).toBe("5,400");
  });
});
