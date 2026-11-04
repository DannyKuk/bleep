import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { approval, detail, event, incident, proposal } from "@/components/incident/fixtures";
import { planRun } from "@/components/incident/plan-run";
import { planTitle, ProposalCard } from "@/components/incident/proposal-card";

describe("ProposalCard", () => {
  it("shows the plan put to the human, with the decision bar while it waits", () => {
    const asked = proposal({ reason: "the plan that was asked" });
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({ reason: "a newer plan in the run" })}
        approval={approval({ proposal: asked })}
        onDecided={vi.fn()}
      />,
    );
    expect(screen.getByText("the plan that was asked")).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps" }));
    expect(steps.getByText("docker.start")).toBeInTheDocument();
    expect(steps.getByText("shop-web-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yes, go ahead" })).toBeEnabled();
  });

  it("shows a character that draws nothing in a step's params as a mark", () => {
    const hidden = proposal({
      steps: [
        {
          action: "mcp.call",
          host_id: "demo",
          target: "shop-web-1",
          params: { arguments: '{"c": "shop\u200b-db-1\u034f"}' },
        },
      ],
    });
    render(
      <ProposalCard incidentId="inc-1" proposal={hidden} approval={null} onDecided={vi.fn()} />,
    );
    const steps = within(screen.getByRole("list", { name: "Steps" }));
    expect(steps.getByText('arguments={"c": "shop\uFFFD-db-1\uFFFD"}')).toBeInTheDocument();
  });

  it("marks every character the guard calls invisible, but drops an emoji presentation selector", () => {
    const hidden = proposal({
      steps: [
        {
          action: "mcp.call",
          host_id: "demo",
          target: "shop-web-1",
          params: { note: "a\ufe00b\u{e0100}c\u2800d\u26a0\ufe0f" },
        },
      ],
    });
    render(
      <ProposalCard incidentId="inc-1" proposal={hidden} approval={null} onDecided={vi.fn()} />,
    );
    const steps = within(screen.getByRole("list", { name: "Steps" }));
    expect(steps.getByText("note=a\uFFFDb\uFFFDc\uFFFDd\u26a0")).toBeInTheDocument();
  });

  it("says no action is proposed, with no buttons", () => {
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({
          kind: "no_action",
          steps: [],
          reason: "A code bug; a restart won't help",
        })}
        approval={null}
        onDecided={vi.fn()}
      />,
    );
    expect(screen.getByText(/No action proposed/)).toBeInTheDocument();
    expect(screen.getByText("A code bug; a restart won't help")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows what the human should do for a no-action plan as Bleep's advice, not as a quote", () => {
    const fix = "Set DATABASE_URL to postgres://db:5432/shop and run `docker compose up -d web`";
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({
          kind: "no_action",
          steps: [],
          reason: "The app is configured with the wrong database host",
          human_instructions: fix,
        })}
        approval={null}
        onDecided={vi.fn()}
      />,
    );
    const advice = screen.getByRole("region", { name: "What to do" });
    expect(advice).toHaveTextContent(fix);
    expect(screen.queryByText(/your instruction/i)).toBeNull();
  });

  it("says why nothing will run when Bleep's checks refused the plan", () => {
    const reason = "Bleep's own checks refused the proposed plan twice, so nothing will run.";
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({
          kind: "no_action",
          steps: [],
          reason,
          refused: true,
          human_instructions: `${reason}\nWhy:\n- step 1: shop-db-1 is protected`,
        })}
        approval={null}
        onDecided={vi.fn()}
      />,
    );
    const why = screen.getByRole("region", { name: "Why nothing will run" });
    expect(why).toHaveTextContent("shop-db-1 is protected");
    expect(screen.queryByRole("region", { name: "What to do" })).toBeNull();
    expect(screen.queryByText(/your instruction/i)).toBeNull();
  });

  it("keeps the refused title for a plan stored before the refused flag", () => {
    const reason = "Bleep's own checks refused the proposed plan twice, so nothing will run.";
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({
          kind: "no_action",
          steps: [],
          reason,
          refused: undefined,
          human_instructions: reason,
        })}
        approval={null}
        onDecided={vi.fn()}
      />,
    );
    expect(screen.getByRole("region", { name: "Why nothing will run" })).toBeInTheDocument();
  });

  it("shows an answer to an earlier plan apart from the new one", () => {
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={proposal({ reason: "the new plan" })}
        approval={approval({
          status: "instructed",
          decision: {
            kind: "instruct",
            text: "Restart the db too",
            via: "telegram",
            at: "2026-11-02T18:05:00Z",
          },
        })}
        onDecided={vi.fn()}
      />,
    );
    expect(screen.getByText("the new plan")).toBeInTheDocument();
    expect(screen.getByText(/Earlier plan/)).toHaveTextContent(/Telegram/);
    expect(screen.queryByRole("button", { name: "Yes, go ahead" })).toBeNull();
  });

  it("shows each step's progress while the plan runs: a spinner, then a check", () => {
    const twoSteps = proposal({
      steps: [
        { action: "docker.start", host_id: "demo", target: "shop-db-1", params: {} },
        { action: "docker.restart", host_id: "demo", target: "shop-api-1", params: {} },
      ],
    });
    const run = planRun([
      event("proposal", {}),
      event("action", { action: "docker.start", target: "shop-db-1", status: "started" }),
      event("action", { action: "docker.start", target: "shop-db-1", status: "done" }),
      event("action", { action: "docker.restart", target: "shop-api-1", status: "started" }),
    ]);
    render(
      <ProposalCard
        incidentId="inc-1"
        proposal={twoSteps}
        approval={null}
        onDecided={vi.fn()}
        run={run}
        status="executing"
      />,
    );
    const [first, second] = within(screen.getByRole("list", { name: "Steps" })).getAllByRole(
      "listitem",
    );
    expect(first).toHaveTextContent("Step 1, done:");
    expect(second).toHaveTextContent("Step 2, running:");
    expect(screen.queryByRole("list", { name: "Checks" })).toBeNull();
  });

  it("shows the planned checks as pending pills while it verifies, then their results", () => {
    const props = {
      incidentId: "inc-1",
      proposal: proposal(),
      approval: null,
      onDecided: vi.fn(),
    };
    const { rerender } = render(<ProposalCard {...props} run={planRun([])} status="verifying" />);
    let pills = within(screen.getByRole("list", { name: "Checks" })).getAllByRole("listitem");
    expect(pills.map((pill) => pill.textContent)).toEqual([
      "Checking:shop-web-1",
      "Checking:http://localhost:8080/",
    ]);

    const checked = planRun([
      event("verification", {
        ok: false,
        attempt: 1,
        checks: [
          { kind: "container", target: "shop-web-1", ok: true, detail: "running" },
          { kind: "url", target: "http://localhost:8080/", ok: false, detail: "HTTP 502" },
        ],
      }),
    ]);
    rerender(<ProposalCard {...props} run={checked} status="verifying" />);
    pills = within(screen.getByRole("list", { name: "Checks" })).getAllByRole("listitem");
    expect(pills.map((pill) => pill.textContent)).toEqual([
      "Passed:shop-web-1running",
      "Failed:http://localhost:8080/HTTP 502",
    ]);
    expect(screen.getByText("Check 1: failed")).toBeInTheDocument();
  });

  it("names the plan card for where the run is", () => {
    expect(planTitle(detail())).toBe("Here is my plan");
    const approved = approval({ status: "approved" });
    expect(
      planTitle(detail({ incident: incident({ status: "executing" }), approval: approved })),
    ).toBe("Doing it now");
    expect(
      planTitle(detail({ incident: incident({ status: "resolved" }), approval: approved })),
    ).toBe("What I did");
    expect(
      planTitle(
        detail({
          incident: incident({ status: "closed" }),
          approval: approval({ status: "rejected" }),
          outcome: "rejected",
        }),
      ),
    ).toBe("The plan you said no to");
    expect(planTitle(detail({ proposal: null, approval: null }))).toBe("My plan");
    const refused = proposal({ kind: "no_action", steps: [], refused: true });
    expect(planTitle(detail({ approval: approval({ proposal: refused }) }))).toBe(
      "No plan I may run",
    );
  });
});
