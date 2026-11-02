import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { event } from "@/components/incident/fixtures";
import { Timeline } from "@/components/incident/timeline";

describe("Timeline", () => {
  it("shows one line per step, done or running, and leaves tool calls out", () => {
    render(
      <Timeline
        events={[
          event("node_started", { node: "gather" }),
          event("node_finished", { node: "gather" }),
          event("node_started", { node: "investigate" }),
          event("tool_call", { name: "container_logs", args: {} }),
        ]}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Gathered context");
    expect(items[1]).toHaveTextContent("Investigating");
    expect(screen.queryByText(/container_logs/)).toBeNull();
  });

  it("names the channel of a decision and shows notices", () => {
    render(
      <Timeline
        events={[
          event("approval_requested", { approval_id: "appr-1", plan_hash: "abc" }),
          event("decision", { kind: "instruct", text: "Check the db", via: "telegram" }),
          event("notice", { text: "The plan could not be sent to a channel; answer it here." }),
        ]}
      />,
    );
    expect(screen.getByText("Asked for approval")).toBeInTheDocument();
    expect(screen.getByText(/Instruction from Telegram/)).toHaveTextContent("Check the db");
    expect(screen.getByText(/could not be sent to a channel/)).toBeInTheDocument();
  });
});
