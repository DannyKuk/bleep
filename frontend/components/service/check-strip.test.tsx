import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CheckStrip } from "@/components/service/check-strip";
import type { UrlCheckPoint } from "@/lib/api-types";
import { setLanguage } from "@/lib/i18n";

const check = (ok: boolean, at: string): UrlCheckPoint => ({
  ok,
  status_code: ok ? 200 : 503,
  latency_ms: 20,
  checked_at: at,
});

describe("CheckStrip", () => {
  it("draws nothing without checks, and puts the tab stop on the newest once they come", () => {
    const { container, rerender } = render(<CheckStrip url="https://a.example/" checks={[]} />);
    expect(container).toBeEmptyDOMElement();

    rerender(
      <CheckStrip
        url="https://a.example/"
        checks={[check(true, "2026-11-05T09:00:00Z"), check(true, "2026-11-05T09:00:30Z")]}
      />,
    );
    expect(screen.getAllByRole("img").map((tick) => tick.tabIndex)).toEqual([-1, 0]);
    expect(screen.getByText("Last 2 checks: all passed")).toBeInTheDocument();
  });

  it("says a single check in the singular", () => {
    render(<CheckStrip url="https://a.example/" checks={[check(false, "2026-11-05T09:00:00Z")]} />);
    expect(
      screen.getByRole("list", {
        name: "Last check: failed, oldest first, for https://a.example/",
      }),
    ).toBeInTheDocument();
  });

  it("says its summary and each tick in German", () => {
    act(() => setLanguage("de"));
    render(
      <CheckStrip
        url="https://a.example/"
        checks={[
          check(true, "2026-11-05T09:00:00Z"),
          { ...check(false, "2026-11-05T09:00:30Z"), status_code: null },
        ]}
      />,
    );
    expect(
      screen.getByRole("list", {
        name: "Letzte 2 Prüfungen: 1 fehlgeschlagen, älteste zuerst, für https://a.example/",
      }),
    ).toBeInTheDocument();
    const [passed, failed] = screen.getAllByRole("img");
    expect(passed.getAttribute("aria-label")).toMatch(/^Bestanden um \d\d:\d\d:\d\d, 200, 20 ms$/);
    expect(failed.getAttribute("aria-label")).toMatch(
      /^Fehlgeschlagen um .*, keine Antwort, 20 ms$/,
    );
  });
});
