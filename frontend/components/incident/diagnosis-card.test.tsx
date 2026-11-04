import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiagnosisCard } from "@/components/incident/diagnosis-card";
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
});
