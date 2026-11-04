import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IncidentStatusBadge } from "@/components/incident/status-badge";
import type { IncidentStatus } from "@/lib/api-types";

describe("IncidentStatusBadge", () => {
  it.each<[IncidentStatus, string]>([
    ["executing", "Executing"],
    ["verifying", "Verifying"],
    ["paused", "Paused"],
  ])("labels %s as %s", (status, label) => {
    render(<IncidentStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
