import { describe, expect, it } from "vitest";

import { formatDateTime, formatSince, formatTime } from "@/lib/format";
import { morningLabel, quietLabel, sinceWords, snoozeUntil } from "@/lib/quiet-api";
import { atHourIn, dayIn, usableZone } from "@/lib/zone";

const VIENNA = "Europe/Vienna";
const NEW_YORK = "America/New_York";
const since = "2026-11-09T11:00:00Z";

/** `hours`:`rest` on a 24 h clock, or as the locale's 12 h clock writes it ("1:00:05 pm"). */
const clock = (hours: number, rest: string) =>
  new RegExp(`(^|\\D)(0?${hours}:${rest}|0?${hours % 12 || 12}:${rest}\\s?[ap]\\.?m)`, "i");

describe("a chosen time zone", () => {
  // Times come from the API in UTC; the same moment shows as each zone's wall clock.
  const at = "2026-11-09T12:00:05Z";

  it("formats one moment as the chosen zone's clock, whatever the browser's", () => {
    expect(formatTime(at, VIENNA)).toMatch(clock(13, "00:05"));
    expect(formatTime(at, NEW_YORK)).toMatch(clock(7, "00:05"));
    expect(formatDateTime(at, VIENNA)).toMatch(clock(13, "00"));
    expect(formatDateTime(at, NEW_YORK)).toMatch(clock(7, "00"));
  });

  it("says today, yesterday and weekdays by the chosen zone's calendar", () => {
    const now = new Date("2026-11-09T04:00:00Z"); // 05:00 Mon in Vienna, 23:00 Sun in New York
    const evening = "2026-11-08T22:30:00Z"; // 23:30 Sun in Vienna, 17:30 Sun in New York
    expect(formatSince(evening, now, VIENNA)).toBe("yesterday 23:30");
    expect(formatSince(evening, now, NEW_YORK)).toBe("17:30");
    expect(formatSince("2026-11-05T10:00:00Z", now, VIENNA)).toBe("Thu 11:00");
    expect(formatSince("2026-10-20T13:02:00Z", now, VIENNA)).toBe("20 Oct 15:02");
    expect(formatSince("2025-12-31T23:30:00Z", now, NEW_YORK)).toBe("31 Dec 2025 18:30");
  });

  it("makes the quiet menu's until 08:00 the next 08:00 in the chosen zone", () => {
    const noonUtc = new Date("2026-11-09T12:00:00Z"); // 13:00 in Vienna, 07:00 in New York
    expect(snoozeUntil("morning", noonUtc, VIENNA).toISOString()).toBe("2026-11-10T07:00:00.000Z");
    expect(morningLabel(noonUtc, VIENNA)).toBe("Until tomorrow 8:00");
    expect(snoozeUntil("morning", noonUtc, NEW_YORK).toISOString()).toBe(
      "2026-11-09T13:00:00.000Z",
    );
    expect(morningLabel(noonUtc, NEW_YORK)).toBe("Until 8:00");
  });

  it("keeps until 08:00 at 08:00 local across a clock change", () => {
    const beforeSpring = new Date("2026-03-28T12:00:00Z"); // Vienna moves to CEST on 29 March
    expect(snoozeUntil("morning", beforeSpring, VIENNA).toISOString()).toBe(
      "2026-03-29T06:00:00.000Z",
    );
    expect(atHourIn("2026-10-25", 8, VIENNA).toISOString()).toBe("2026-10-25T07:00:00.000Z");
  });

  it("shows a stored snooze in the zone chosen after it was set", () => {
    const until = snoozeUntil("morning", new Date("2026-11-09T12:00:00Z"), VIENNA).toISOString();
    const view = { scope: "service", mode: "snooze", until, since } as const;
    const now = new Date("2026-11-09T12:00:00Z");
    expect(quietLabel(view, now, VIENNA)).toBe("Snoozed until tomorrow 8:00");
    expect(quietLabel(view, now, NEW_YORK)).toBe("Snoozed until tomorrow 2:00");
    expect(sinceWords(new Date(since), now, NEW_YORK)).toBe("6:00");
  });

  it("names days by the zone, not by UTC", () => {
    const lateEvening = new Date("2026-11-09T23:30:00Z");
    expect(dayIn(lateEvening, VIENNA)).toBe("2026-11-10");
    expect(dayIn(lateEvening, NEW_YORK)).toBe("2026-11-09");
    expect(dayIn(lateEvening, VIENNA, -1)).toBe("2026-11-09");
  });
});

describe("no time zone chosen", () => {
  it("shows the browser's own zone", () => {
    const at = new Date(2026, 10, 9, 18, 1, 5).toISOString(); // 18:01:05 on the browser's clock
    expect(formatTime(at, undefined)).toBe(formatTime(at));
    expect(formatTime(at)).toMatch(clock(18, "01:05"));
    expect(formatSince(at, new Date(2026, 10, 9, 20, 0), undefined)).toBe("18:01");
    const night = new Date(2026, 10, 5, 1, 0);
    expect(snoozeUntil("morning", night)).toEqual(new Date(2026, 10, 5, 8, 0));
  });

  it("falls back to the browser's zone for a name the browser does not know", () => {
    expect(usableZone("Mars/Olympus")).toBeUndefined();
    expect(usableZone(null)).toBeUndefined();
    expect(usableZone(VIENNA)).toBe(VIENNA);
  });
});
