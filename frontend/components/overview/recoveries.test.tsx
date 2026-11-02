import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecoveriesView } from "@/components/overview/recoveries";

describe("RecoveriesView", () => {
  it("lists each recovery with its container, host and explanation", () => {
    render(
      <RecoveriesView
        recoveries={[
          {
            id: 1,
            host_id: "demo",
            service_id: "demo/compose:shop",
            container: "shop-worker-1",
            at: "2026-11-02T18:00:00Z",
            explanation: "The worker lost its database connection and came back on restart.",
          },
        ]}
      />,
    );
    expect(screen.getByText("shop-worker-1")).toBeInTheDocument();
    expect(screen.getByText("demo")).toBeInTheDocument();
    expect(screen.getByText(/lost its database connection/)).toBeInTheDocument();
  });

  it("says when nothing has recovered on its own", () => {
    render(<RecoveriesView recoveries={[]} />);
    expect(screen.getByText(/No self-recoveries yet/)).toBeInTheDocument();
  });
});
