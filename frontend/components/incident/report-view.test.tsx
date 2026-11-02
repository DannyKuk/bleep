import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { report } from "@/components/incident/fixtures";
import { ReportView } from "@/components/incident/report-view";

describe("ReportView", () => {
  it("shows the outcome, summary, cause, action, decision and follow-ups", () => {
    render(<ReportView report={report()} outcome="resolved" />);
    expect(screen.getByText("Fixed and verified")).toHaveClass("text-health-ok");
    expect(screen.getByText(/Bleep started it again/)).toBeInTheDocument();
    expect(screen.getByText(/restart policy is no/)).toBeInTheDocument();
    expect(screen.getByText("docker.start shop-web-1 on demo: done")).toBeInTheDocument();
    expect(screen.getAllByText("Approved on Telegram")[0]).toBeInTheDocument();
    const followUps = within(screen.getByRole("list", { name: "Follow-ups" }));
    expect(followUps.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Set restart: unless-stopped on shop-web.",
      "Find out who stopped it.",
    ]);
  });

  it("lists the report's timeline in its order, with times", () => {
    render(<ReportView report={report()} outcome="resolved" />);
    const items = within(screen.getByRole("list", { name: "Report timeline" })).getAllByRole(
      "listitem",
    );
    expect(items.map((item) => item.querySelector("span")?.textContent)).toEqual([
      "Signal: container died (shop-web-1)",
      "Approved on Telegram",
      "Check 1 passed",
    ]);
    expect(items[0].querySelector("time")).toHaveAttribute("datetime", "2026-11-02T18:00:00Z");
  });

  it("says so when nothing was done and nobody was asked", () => {
    render(
      <ReportView
        report={report({
          outcome: "No action proposed, nothing was done",
          action_taken: null,
          human_decision: null,
          follow_ups: [],
          timeline: [],
        })}
        outcome="no_action"
      />,
    );
    expect(screen.getByText("No action proposed, nothing was done")).toHaveClass(
      "text-muted-foreground",
    );
    expect(screen.getByText("Nothing ran.")).toBeInTheDocument();
    expect(screen.getByText("You were not asked.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Follow-ups" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Report timeline" })).toBeNull();
  });

  it("marks an unverified action as not confirmed", () => {
    render(
      <ReportView
        report={report({ outcome: "Action taken, not verified" })}
        outcome="unverified"
      />,
    );
    expect(screen.getByText("Action taken, not verified")).toHaveClass("text-health-degraded");
  });
});
