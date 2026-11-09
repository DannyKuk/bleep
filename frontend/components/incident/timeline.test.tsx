import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LOOK, type Mark } from "@/components/incident/colour-code";
import { event, incident } from "@/components/incident/fixtures";
import { ProgressBar } from "@/components/incident/progress-bar";
import { progressSteps } from "@/components/incident/progress";
import { Timeline } from "@/components/incident/timeline";
import type { Incident, IncidentEvent } from "@/lib/api-types";

const started = (node: string) => event("node_started", { node });
const finished = (node: string) => event("node_finished", { node });
const ran = (...nodes: string[]) => nodes.flatMap((node) => [started(node), finished(node)]);
const step = (status: string) =>
  event("action", { action: "docker.start", target: "shop-web-1", status });
const check = (ok: boolean, attempt = 1) => event("verification", { ok, attempt, checks: [] });

function timelineItems() {
  return within(screen.getByRole("list", { name: "Timeline" })).getAllByRole("listitem");
}

/** Each line's visible text and the state it says it is in. */
function lines(): [string, string | null][] {
  return timelineItems().map((item) => [
    item.querySelector(":scope > span:not([aria-hidden]):not(.sr-only)")!.textContent!,
    item.getAttribute("data-state"),
  ]);
}

/** The timeline and the progress bar of one incident, as the incident page shows them. */
function renderBoth(events: IncidentEvent[], status: Incident["status"]) {
  render(
    <>
      <ProgressBar steps={progressSteps(events, status)} />
      <Timeline
        events={events}
        incident={incident({ status, closed_at: "2026-11-02T18:06:00Z" })}
      />
    </>,
  );
}

function barStates(): Record<string, string | null> {
  const items = within(screen.getByRole("list", { name: "Progress" })).getAllByRole("listitem");
  return Object.fromEntries(
    items.map((item) => [item.textContent!.split(",")[0], item.getAttribute("data-state")]),
  );
}

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

  it("says what This is expected recorded", () => {
    render(
      <Timeline
        events={[
          event("expected", { text: "You said this is expected. A may sleep.", via: "telegram" }),
        ]}
      />,
    );
    expect(screen.getByText("You said this is expected. A may sleep.")).toBeInTheDocument();
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

  it("marks each line by what it means: now, done, a warning, a note", () => {
    render(
      <Timeline
        events={[
          ...ran("gather"),
          event("proposal", { kind: "act" }),
          event("approval_requested", { approval_id: "appr-1", plan_hash: "abc" }),
          event("decision", { kind: "approve", text: null, via: "dashboard" }),
          step("started"),
          step("done"),
          check(false, 1),
          check(true, 2),
          event("notice", { text: "Telegram did not answer." }),
          started("investigate"),
        ]}
      />,
    );
    expect(lines()).toEqual([
      ["Gathered context", "done"],
      ["Proposed a plan", "info"],
      ["Asked for approval", "info"],
      ["Approved on the dashboard", "done"],
      ["docker.start shop-web-1: started", "info"],
      ["docker.start shop-web-1: done", "done"],
      ["Check 1 failed", "warning"],
      ["Check 2 passed", "done"],
      ["Telegram did not answer.", "warning"],
      ["Investigating", "current"],
    ]);
  });

  it("says every line's state to a screen reader and draws it from the shared colour code", () => {
    render(
      <Timeline
        events={[...ran("gather"), check(false), step("failed"), started("investigate")]}
        incident={incident({ status: "investigating" })}
      />,
    );
    for (const item of timelineItems()) {
      const look = LOOK[item.getAttribute("data-state") as Mark];
      expect(item.querySelector(".sr-only")).toHaveTextContent(`, ${look.word}`);
      const marker = item.querySelector("[aria-hidden]")!;
      if (look.cross) expect(marker.querySelector("svg")).toHaveClass(look.ink);
      else expect(marker.firstElementChild!.className).toContain(look.fill);
    }
  });

  it("shows Approval waiting for the human as happening now, in bold, not as gone wrong", () => {
    render(<Timeline events={[...ran("propose"), started("approval")]} />);
    const waiting = timelineItems()[1];
    expect(waiting).toHaveTextContent("Waiting for your answer, now");
    expect(waiting).toHaveAttribute("data-state", "current");
    expect(screen.getByText("Waiting for your answer")).toHaveClass("font-extrabold");
  });

  it("starts with what opened the incident as a marker of its own, not as a failure", () => {
    render(<Timeline events={[]} incident={incident({ status: "investigating" })} />);
    const [opened] = timelineItems();
    expect(opened).toHaveTextContent("shop-web-1 died, opened the incident");
    expect(opened).toHaveAttribute("data-state", "opened");
  });

  it("marks a failed run red where it went wrong and where it stopped, as the bar does", () => {
    renderBoth(
      [
        ...ran("gather", "investigate", "diagnose", "propose", "approval"),
        started("execute"),
        step("started"),
        step("failed"),
        finished("execute"),
        started("verify"),
      ],
      "failed",
    );
    expect(lines().slice(6)).toEqual([
      ["Ran the plan", "failed"],
      ["docker.start shop-web-1: started", "info"],
      ["docker.start shop-web-1: failed", "failed"],
      ["Checking the service", "stopped"],
      ["Failed", "failed"],
    ]);
    expect(barStates()).toMatchObject({ Fix: "failed", Check: "stopped" });
    expect(screen.getByText("Checking the service")).not.toHaveClass("font-extrabold");
  });

  it("marks a declined plan as the human's no, not as a failure, as the bar does", () => {
    renderBoth(
      [
        ...ran("gather", "investigate", "diagnose", "propose"),
        started("approval"),
        event("decision", { kind: "reject", text: null, via: "telegram" }),
        started("approval"),
        finished("approval"),
        ...ran("report"),
      ],
      "closed",
    );
    expect(lines().slice(5)).toEqual([
      ["Waiting for your answer", "done"],
      ["Rejected on Telegram", "declined"],
      ["Answered", "declined"],
      ["Wrote the report", "done"],
      ["Closed", "info"],
    ]);
    expect(barStates()).toMatchObject({ "Your yes": "declined", Report: "done" });
    expect(timelineItems().some((item) => item.matches("[data-state=failed]"))).toBe(false);
  });

  it("marks a resolved incident green from the fix to the end, as the bar does", () => {
    renderBoth(
      [
        ...ran("gather", "investigate", "diagnose", "propose", "approval"),
        started("execute"),
        step("started"),
        step("done"),
        finished("execute"),
        started("verify"),
        check(true),
        finished("verify"),
        ...ran("report"),
      ],
      "resolved",
    );
    expect(lines().slice(6)).toEqual([
      ["Ran the plan", "done"],
      ["docker.start shop-web-1: started", "info"],
      ["docker.start shop-web-1: done", "done"],
      ["Checked the service", "done"],
      ["Check 1 passed", "done"],
      ["Wrote the report", "done"],
      ["Resolved", "done"],
    ]);
    expect(Object.values(barStates()).every((state) => state === "done")).toBe(true);
  });

  it("shows a step a closed incident never finished as not done, as the bar does", () => {
    renderBoth([...ran("gather"), started("investigate")], "closed");
    expect(lines()).toEqual([
      ["shop-web-1 died", "opened"],
      ["Gathered context", "done"],
      ["Investigating", "todo"],
      ["Closed", "info"],
    ]);
    expect(barStates()).toMatchObject({ Gather: "done", Investigate: "todo" });
  });

  it("marks Check failed once Verify ends without a passing check, as the bar does", () => {
    renderBoth([started("verify"), check(false), finished("verify")], "investigating");
    expect(lines().slice(1)).toEqual([
      ["Checked the service", "failed"],
      ["Check 1 failed", "warning"],
    ]);
    expect(barStates()).toMatchObject({ Check: "failed" });
  });

  it("stops showing the wait as running once the plan was answered and the node resumed", () => {
    render(
      <Timeline
        events={[
          started("approval"),
          event("decision", { kind: "approve", text: null, via: "telegram" }),
          started("approval"),
          finished("approval"),
        ]}
      />,
    );
    expect(lines()).toEqual([
      ["Waiting for your answer", "done"],
      ["Approved on Telegram", "done"],
      ["Answered", "done"],
    ]);
  });

  it("ends the wait as soon as the answer arrives", () => {
    render(
      <Timeline
        events={[
          started("approval"),
          event("decision", { kind: "reject", text: null, via: "telegram" }),
        ]}
      />,
    );
    const [waited] = timelineItems();
    expect(waited).toHaveAttribute("data-state", "done");
    expect(waited.querySelector(".motion-safe\\:animate-pulse")).toBeNull();
  });
});
