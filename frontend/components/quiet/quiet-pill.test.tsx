import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QuietPill } from "@/components/quiet/quiet-pill";
import { morningLabel, quietLabel, snoozeUntil } from "@/lib/quiet-api";

const NOW = new Date(2026, 10, 4, 15, 30);
const since = "2026-11-04T15:00:00Z";

describe("snoozeUntil", () => {
  it("gives 1 h, 4 h and the next 08:00 in local time", () => {
    expect(snoozeUntil("1h", NOW).getTime()).toBe(NOW.getTime() + 3600_000);
    expect(snoozeUntil("4h", NOW).getTime()).toBe(NOW.getTime() + 4 * 3600_000);
  });

  it("makes morning the next 08:00: this morning in the small hours, else tomorrow's", () => {
    const night = new Date(2026, 10, 5, 1, 0);
    const day = new Date(2026, 10, 5, 9, 0);
    expect(snoozeUntil("morning", night)).toEqual(new Date(2026, 10, 5, 8, 0));
    expect(snoozeUntil("morning", day)).toEqual(new Date(2026, 10, 6, 8, 0));
    expect(morningLabel(night, undefined, "en")).toBe("Until 8:00");
    expect(morningLabel(day, undefined, "en")).toBe("Until tomorrow 8:00");
    const pill = (now: Date) =>
      quietLabel(
        {
          scope: "service",
          mode: "snooze",
          until: snoozeUntil("morning", now).toISOString(),
          since,
        },
        now,
        undefined,
        "en",
      );
    expect(pill(night)).toBe("Snoozed until 8:00");
    expect(pill(day)).toBe("Snoozed until tomorrow 8:00");
  });
});

describe("quietLabel", () => {
  it("says Left alone, Snoozed until HH:MM, tomorrow, and Host … for a host's quiet", () => {
    const at = (d: Date) => d.toISOString();
    const snooze = (until: Date, scope: "service" | "host" = "service") =>
      ({ scope, mode: "snooze", until: at(until), since }) as const;
    expect(
      quietLabel(
        { scope: "service", mode: "leave_alone", until: null, since },
        NOW,
        undefined,
        "en",
      ),
    ).toBe("Left alone");
    expect(quietLabel(snooze(new Date(2026, 10, 4, 18, 0)), NOW, undefined, "en")).toBe(
      "Snoozed until 18:00",
    );
    expect(quietLabel(snooze(new Date(2026, 10, 5, 8, 0)), NOW, undefined, "en")).toBe(
      "Snoozed until tomorrow 8:00",
    );
    expect(
      quietLabel({ scope: "host", mode: "leave_alone", until: null, since }, NOW, undefined, "en"),
    ).toBe("Host left alone");
    expect(quietLabel(snooze(new Date(2026, 10, 4, 18, 0), "host"), NOW, undefined, "en")).toBe(
      "Host snoozed until 18:00",
    );
  });
});

describe("QuietPill", () => {
  it("shows the label", () => {
    render(<QuietPill quiet={{ scope: "service", mode: "leave_alone", until: null, since }} />);
    expect(screen.getByText("Left alone")).toBeInTheDocument();
  });
});
