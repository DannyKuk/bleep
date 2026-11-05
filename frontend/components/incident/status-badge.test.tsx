import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IncidentStatusBadge } from "@/components/incident/status-badge";
import type { IncidentStatus } from "@/lib/api-types";

describe("IncidentStatusBadge", () => {
  it.each<[IncidentStatus, string]>([
    ["executing", "Executing"],
    ["verifying", "Verifying"],
    ["paused", "Paused"],
    ["waiting", "Waiting for the host"],
    ["awaiting_approval", "Needs your yes"],
    ["open", "Open"],
    ["investigating", "Investigating"],
    ["resolved", "Resolved"],
    ["closed", "Closed"],
    ["failed", "Failed"],
  ])("labels %s as %s", (status, label) => {
    render(<IncidentStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
