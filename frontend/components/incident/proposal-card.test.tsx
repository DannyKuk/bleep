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
