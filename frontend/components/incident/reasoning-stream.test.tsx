import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { event } from "@/components/incident/fixtures";
import { ReasoningStream } from "@/components/incident/reasoning-stream";

describe("ReasoningStream", () => {
  it("shows each tool call with its name and arguments", () => {
    render(
      <ReasoningStream
        events={[
          event("node_started", { node: "investigate" }),
          event("tool_call", {
            name: "container_logs",
            args: { container: "shop-web-1", lines: 200 },
          }),
          event("tool_call", { name: "list_containers", args: {} }),
        ]}
      />,
    );
    const calls = within(screen.getByRole("list", { name: "Tool calls" })).getAllByRole("listitem");
    expect(calls).toHaveLength(2);
    expect(within(calls[0]).getByText("container_logs")).toBeInTheDocument();
    expect(within(calls[0]).getByText("container=shop-web-1")).toBeInTheDocument();
    expect(within(calls[0]).getByText("lines=200")).toBeInTheDocument();
    expect(within(calls[1]).getByText("list_containers")).toBeInTheDocument();
  });

  it("folds each result under its call, closed until opened", async () => {
    render(
      <ReasoningStream
        events={[
          event("tool_call", { name: "container_logs", args: { container: "shop-web-1" } }),
          event("tool_call", { name: "inspect_container", args: { container: "shop-web-1" } }),
          event("tool_result", {
            name: "container_logs",
            chars: 5120,
            summary_first_line: "Traceback (most recent call last):",
          }),
        ]}
      />,
    );
    const [logs, inspect] = screen.getAllByRole("listitem");
    const folded = within(logs).getByText("Traceback (most recent call last):").closest("details");
    expect(folded).not.toHaveAttribute("open");
    expect(within(logs).getByText(/5,120 characters/)).toBeInTheDocument();
    await userEvent.click(within(logs).getByText(/Result/));
    expect(folded).toHaveAttribute("open");
    // The second call has no result yet.
    expect(within(inspect).getByText("Running")).toBeInTheDocument();
    expect(within(inspect).queryByText(/Result/)).toBeNull();
  });

  it("pairs results with calls of the same name in order", () => {
    render(
      <ReasoningStream
        events={[
          event("tool_call", { name: "container_logs", args: { container: "a" } }),
          event("tool_call", { name: "container_logs", args: { container: "b" } }),
          event("tool_result", { name: "container_logs", chars: 1, summary_first_line: "from a" }),
          event("tool_result", { name: "container_logs", chars: 1, summary_first_line: "from b" }),
        ]}
      />,
    );
    const [first, second] = screen.getAllByRole("listitem");
    expect(within(first).getByText("from a")).toBeInTheDocument();
    expect(within(second).getByText("from b")).toBeInTheDocument();
  });

  it("says so when no tool was called", () => {
    render(<ReasoningStream events={[event("node_started", { node: "gather" })]} />);
    expect(screen.getByText(/No tool calls yet/)).toBeInTheDocument();
  });
});
