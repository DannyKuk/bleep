import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HealthBadge } from "@/components/overview/health-badge";
import type { Health } from "@/lib/api-types";

const cases: [Health, string][] = [
  ["ok", "OK"],
  ["degraded", "Degraded"],
  ["down", "Down"],
  ["stopped", "Stopped"],
  ["unknown", "Unknown"],
];

describe("HealthBadge", () => {
  it.each(cases)("renders label, icon and colour token for %s", (health, label) => {
    render(<HealthBadge health={health} />);
    const badge = screen.getByText(label).closest("[data-health]") as HTMLElement;
    expect(badge).toHaveAttribute("data-health", health);
    expect(badge.className).toContain(`text-health-${health}`);
    expect(badge.className).toContain(`bg-health-${health}`);
    expect(badge.querySelector("svg")).not.toBeNull();
  });
});
