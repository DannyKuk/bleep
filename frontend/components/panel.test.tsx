import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Panel } from "@/components/panel";

describe("Panel", () => {
  it("titles the section and shows its content", () => {
    render(<Panel title="What I found">body</Panel>);
    expect(screen.getByRole("region", { name: "What I found" })).toHaveTextContent("body");
  });

  it("gets the accent border only while it waits for the human", () => {
    const { rerender } = render(<Panel title="Plan">x</Panel>);
    expect(screen.getByRole("region").className).not.toContain("border-accent");
    rerender(
      <Panel title="Plan" awaitingYou>
        x
      </Panel>,
    );
    expect(screen.getByRole("region").className).toContain("border-accent");
  });
});
