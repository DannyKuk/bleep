import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { incident } from "@/components/incident/fixtures";
import { IncidentListView } from "@/components/incident/incident-list";

describe("IncidentListView", () => {
  it("links each incident to its page with its status and subject", () => {
    render(
      <IncidentListView
        incidents={[
          incident(),
          incident({
            id: "inc-2",
            title: "Disk 93% full",
            status: "resolved",
            subject: { kind: "host", host_id: "spare", service_id: null },
          }),
        ]}
      />,
    );
    expect(screen.getByRole("link", { name: /shop-web-1 died/ })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
    expect(screen.getByText("Needs your yes")).toBeInTheDocument();
    expect(screen.getByText("demo/compose:shop")).toBeInTheDocument();
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(screen.getByText("host spare")).toBeInTheDocument();
  });

  it("shows the empty state", () => {
    render(<IncidentListView incidents={[]} />);
    expect(screen.getByText(/No incidents yet/)).toBeInTheDocument();
  });
});
