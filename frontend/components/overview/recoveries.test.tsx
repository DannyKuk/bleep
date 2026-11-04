import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecoveriesView } from "@/components/overview/recoveries";
import type { RecoveryEvent } from "@/lib/api-types";

function recovery(overrides: Partial<RecoveryEvent> = {}): RecoveryEvent {
  return {
    id: 1,
    host_id: "demo",
    service_id: "demo/compose:shop",
    container: "shop-worker-1",
    at: "2026-11-02T18:00:00Z",
    explanation: "The worker lost its database connection and came back on restart.",
    ...overrides,
  };
}

describe("RecoveriesView", () => {
  it("lists each recovery with its container, host and explanation", () => {
    render(<RecoveriesView recoveries={[recovery()]} />);
    expect(screen.getByText("shop-worker-1")).toBeInTheDocument();
    expect(screen.getByText("demo")).toBeInTheDocument();
    expect(screen.getByText(/lost its database connection/)).toBeInTheDocument();
  });

  it("links the container to its service's page when the service is known", () => {
    render(
      <RecoveriesView
        recoveries={[recovery(), recovery({ id: 2, container: "loose-1", service_id: null })]}
      />,
    );
    expect(screen.getByRole("link", { name: "shop-worker-1" })).toHaveAttribute(
      "href",
      "/services/demo%2Fcompose%3Ashop",
    );
    expect(screen.queryByRole("link", { name: "loose-1" })).toBeNull();
    expect(screen.getByText("loose-1")).toBeInTheDocument();
  });

  it("says when nothing has fixed itself", () => {
    render(<RecoveriesView recoveries={[]} />);
    expect(screen.getByText(/Nothing has fixed itself yet/)).toBeInTheDocument();
  });
});
