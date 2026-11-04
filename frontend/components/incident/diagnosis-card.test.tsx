import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DiagnosisCard, firstSentences } from "@/components/incident/diagnosis-card";
import { diagnosis } from "@/components/incident/fixtures";

describe("DiagnosisCard", () => {
  it("shows the cause, category, confidence and evidence", () => {
    render(<DiagnosisCard diagnosis={diagnosis()} />);
    expect(screen.getByText(/restart policy is no/)).toBeInTheDocument();
    expect(screen.getByText("config")).toBeInTheDocument();
    expect(screen.getByText("82% sure")).toBeInTheDocument();
    expect(screen.getByText("inspect_container")).toBeInTheDocument();
    expect(screen.getByText("RestartPolicy is no, exit code 137")).toBeInTheDocument();
    expect(screen.queryByText(/I am not sure/)).toBeNull();
  });

  it("shows the 'I am not sure' banner on an unsure diagnosis", () => {
    render(<DiagnosisCard diagnosis={diagnosis({ unsure: true, confidence: 0.4 })} />);
    expect(screen.getByRole("note")).toHaveTextContent("I am not sure");
    expect(screen.getByText("40% sure")).toBeInTheDocument();
  });

  it("keeps the phone's version to two sentences, with the rest and the evidence folded", async () => {
    const cause =
      "shop-db ran out of memory. shop-api 1.2.3 cannot reach it. worker struggles as well.";
    const { container } = render(
      <DiagnosisCard diagnosis={diagnosis({ root_cause: cause, unsure: true })} short />,
    );
    expect(
      screen.getByText("shop-db ran out of memory. shop-api 1.2.3 cannot reach it."),
    ).toBeVisible();
    expect(screen.getByRole("note")).toHaveTextContent("I am not sure");
    expect(screen.getByText("82% sure · config")).toBeInTheDocument();
    const more = container.querySelector("details")!;
    expect(more).not.toHaveAttribute("open");
    await userEvent.click(screen.getByText("More"));
    expect(more).toHaveAttribute("open");
    expect(screen.getByText("worker struggles as well.")).toBeInTheDocument();
    expect(screen.getByText("RestartPolicy is no, exit code 137")).toBeInTheDocument();
  });

  it("splits sentences only at a stop followed by a space", () => {
    expect(firstSentences("Version 1.2.3 broke it. db:5432 refused. Third.", 2)).toEqual([
      "Version 1.2.3 broke it. db:5432 refused.",
      "Third.",
    ]);
    expect(firstSentences("No stop at all", 2)).toEqual(["No stop at all", ""]);
  });
});
