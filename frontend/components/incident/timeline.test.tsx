import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { event, incident } from "@/components/incident/fixtures";
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
          event("report", { summary: "shop-web was started again." }),
        ]}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Gathered context");
    expect(items[1]).toHaveTextContent("Investigating");
    expect(screen.queryByText(/container_logs/)).toBeNull();
    expect(screen.queryByText("report")).toBeNull();
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

  it("shows each step of the plan as it starts and ends, and each check", () => {
    render(
      <Timeline
        events={[
          event("action", { action: "docker.restart", target: "shop-db-1", status: "started" }),
          event("action", { action: "docker.restart", target: "shop-db-1", status: "failed" }),
          event("action", { action: "docker.start", target: "shop-web-1", status: "skipped" }),
          event("verification", { ok: false, attempt: 1, checks: [] }),
          event("verification", { ok: true, attempt: 2, checks: [] }),
        ]}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("docker.restart shop-db-1: started"),
      expect.stringContaining("docker.restart shop-db-1: failed"),
      expect.stringContaining("docker.start shop-web-1: skipped"),
      expect.stringContaining("Check 1 failed"),
      expect.stringContaining("Check 2 passed"),
    ]);
  });

  it("colours each line's dot by what it means", () => {
    render(
      <Timeline
        events={[
          event("node_started", { node: "gather" }),
          event("node_finished", { node: "gather" }),
          event("node_started", { node: "investigate" }),
          event("node_started", { node: "approval" }),
          event("decision", { kind: "approve", text: null, via: "dashboard" }),
          event("action", { action: "docker.start", target: "shop-web-1", status: "failed" }),
          event("verification", { ok: true, attempt: 1, checks: [] }),
        ]}
      />,
    );
    const dots = within(screen.getByRole("list", { name: "Timeline" }))
      .getAllByRole("listitem")
      .map((item) => item.querySelector("span[aria-hidden]")!.className);
    expect(dots[0]).toContain("bg-line");
    expect(dots[1]).toContain("bg-warn");
    expect(dots[2]).toContain("bg-down");
    expect(dots[3]).toContain("bg-accent");
    expect(dots[4]).toContain("bg-down");
    expect(dots[5]).toContain("bg-ok");
  });

  it("starts with what opened the incident and ends with how it ended", () => {
    render(
      <Timeline
        events={[
          event("node_started", { node: "report" }),
          event("node_finished", { node: "report" }),
        ]}
        incident={incident({ status: "resolved", closed_at: "2026-11-02T18:06:00Z" })}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("shop-web-1 died"),
      expect.stringContaining("Wrote the report"),
      expect.stringContaining("Resolved"),
    ]);
    expect(items[0].querySelector("span[aria-hidden]")).toHaveClass("bg-down");
    expect(items[2].querySelector("span[aria-hidden]")).toHaveClass("bg-ok");
  });

  it("stops showing the wait as running once the plan was answered and the node resumed", () => {
    render(
      <Timeline
        events={[
          event("node_started", { node: "approval" }),
          event("decision", { kind: "approve", text: null, via: "telegram" }),
          event("node_started", { node: "approval" }),
          event("node_finished", { node: "approval" }),
        ]}
      />,
    );
    const [waited, , answered] = within(
      screen.getByRole("list", { name: "Timeline" }),
    ).getAllByRole("listitem");
    expect(waited).toHaveTextContent("Waiting for your answer");
    expect(waited.querySelector("span[aria-hidden]")).toHaveClass("bg-line");
    expect(answered).toHaveTextContent("Answered");
  });

  it("shows nothing as running once the incident ended, and where a failed run stopped", () => {
    render(
      <Timeline
        events={[event("node_started", { node: "investigate" })]}
        incident={incident({ status: "failed", closed_at: "2026-11-02T18:06:00Z" })}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
    expect(items[1]).toHaveTextContent("Investigating");
    expect(items[1].querySelector("span[aria-hidden]")).toHaveClass("bg-down");
    expect(items[1].querySelector("span[aria-hidden]")).not.toHaveClass(
      "motion-safe:animate-pulse",
    );
    expect(items[2]).toHaveTextContent("Failed");
  });
});
