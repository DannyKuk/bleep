import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { approval, proposal } from "@/components/incident/fixtures";
import { ProposalCard } from "@/components/incident/proposal-card";

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
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
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
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});
