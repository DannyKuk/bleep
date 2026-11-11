import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { incident, report } from "@/components/incident/fixtures";
import { ReportView } from "@/components/incident/report-view";

describe("ReportView", () => {
  it("shows the outcome, summary, cause, action, decision and follow-ups", () => {
    render(<ReportView report={report()} outcome="resolved" />);
    expect(screen.getByText("Fixed and verified")).toHaveClass("text-ok");
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
          steps: [],
          human_decision: null,
          follow_ups: [],
          timeline: [],
        })}
        outcome="no_action"
      />,
    );
    expect(screen.getByText("No action proposed, nothing was done")).toHaveClass("text-stop");
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
    expect(screen.getByText("Action taken, not verified")).toHaveClass("text-warn");
  });

  it("labels the sections in document order and shows the text as written", () => {
    render(<ReportView report={report()} outcome="resolved" />);
    const labels = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(labels).toEqual([
      "In short",
      "Why it happened",
      "What I did",
      "Your decision",
      "Timeline",
      "Follow-ups",
    ]);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("shop-web-1 died");
  });

  it("puts the incident's short name in front of the report's title (#397)", () => {
    render(
      <ReportView
        report={report()}
        outcome="resolved"
        incident={incident({ short_name: "shop #14" })}
      />,
    );
    const title = screen.getByRole("heading", { level: 2 });
    expect(title).toHaveTextContent(`shop #14 · ${report().title}`);
    expect(within(title).getByText("shop #14")).toHaveClass("font-mono", "text-ink-2");
  });

  it("shows the report's title alone for an incident without a short name, or none", () => {
    for (const given of [incident(), undefined]) {
      const { unmount } = render(
        <ReportView report={report()} outcome="resolved" incident={given} />,
      );
      expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
        new RegExp(`^${report().title}$`),
      );
      unmount();
    }
  });

  it("shows the subject, times and cost when the incident is given", () => {
    render(
      <ReportView
        report={report()}
        outcome="resolved"
        incident={incident({ cost_usd: 0.0421, closed_at: "2026-11-02T18:05:00Z" })}
      />,
    );
    expect(screen.getByText("$0.042")).toBeInTheDocument();
  });

  it("puts the follow-ups of a no-action report under What you can do", () => {
    render(
      <ReportView
        report={report({
          action_taken: null,
          steps: [],
          follow_ups: ["Set restart: unless-stopped."],
        })}
        outcome="no_action"
      />,
    );
    const list = within(screen.getByRole("list", { name: "What you can do" }));
    expect(list.getByText("Set restart: unless-stopped.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Follow-ups" })).toBeNull();
  });

  it("shows a look that found the service healthy as good news, not as Needs you (#421)", () => {
    const { container } = render(
      <ReportView
        report={report({
          outcome: "Healthy when checked, nothing to do",
          action_taken: null,
          steps: [],
          follow_ups: [],
        })}
        outcome="healthy"
      />,
    );
    const pill = screen.getByText("Healthy when checked, nothing to do");
    expect(pill).toHaveClass("text-ok");
    expect(pill).not.toHaveClass("text-warn");
    expect(container.querySelector("[data-mood]")).toHaveAttribute("data-mood", "all_good");
  });

  it("never gives an unfixed outcome the green of a resolved one", () => {
    for (const outcome of [
      "rejected",
      "no_action",
      "unverified",
      "budget",
      "replan_limit",
    ] as const) {
      const { unmount } = render(
        <ReportView report={report({ outcome: "x" })} outcome={outcome} />,
      );
      expect(screen.getByText("x")).not.toHaveClass("text-ok");
      unmount();
    }
  });

  it.each([
    ["done", "text-ok"],
    ["failed", "text-down"],
    ["refused", "text-down"],
    ["skipped", "text-ink-2"],
  ] as const)("marks a step by its status field, %s, whatever words say it", (status, tone) => {
    render(
      <ReportView
        report={report({
          action_taken: "docker.start shop-web auf demo: etwas",
          steps: [{ text: "docker.start shop-web auf demo: etwas", status }],
        })}
        outcome="unverified"
      />,
    );
    const row = within(screen.getByRole("list", { name: "Steps" })).getByRole("listitem");
    expect(row).toHaveTextContent("docker.start shop-web auf demo: etwas");
    expect(row.querySelector("svg")).toHaveClass(tone);
  });

  it("shows a report stored before the steps field as rows with no status mark", () => {
    render(
      <ReportView
        report={report({
          action_taken: "docker.start shop-web on demo: done; docker.start shop-db on demo: failed",
          steps: [],
        })}
        outcome="unverified"
      />,
    );
    const rows = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "docker.start shop-web on demo: done",
      "docker.start shop-db on demo: failed",
    ]);
    for (const row of rows) expect(row.querySelector("svg")).toHaveClass("text-ink-2");
  });

  it("shows each step of a mixed run on its own row, never a check for a failed one", () => {
    render(
      <ReportView
        report={report({
          action_taken:
            "docker.restart shop-web on demo: failed; docker.start shop-db on demo: skipped",
          steps: [
            { text: "docker.restart shop-web on demo: failed", status: "failed" },
            { text: "docker.start shop-db on demo: skipped", status: "skipped" },
          ],
        })}
        outcome="unverified"
      />,
    );
    const rows = within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "docker.restart shop-web on demo: failed",
      "docker.start shop-db on demo: skipped",
    ]);
    expect(rows[0].querySelector("svg")).toHaveClass("text-down");
    expect(rows[1].querySelector("svg")).toHaveClass("text-ink-2");
  });
});
