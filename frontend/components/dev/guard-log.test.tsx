import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, stubBackend } from "@/components/dev/fixtures";
import { GuardLogPanel } from "@/components/dev/guard-log";
import type { GuardEntry } from "@/lib/api-types";

const entry = (overrides: Partial<GuardEntry> = {}): GuardEntry => ({
  id: 1,
  incident_id: "inc_1",
  kind: "refused_command",
  detail: "rm -rf /",
  at: "2026-11-03T10:00:00Z",
  ...overrides,
});

describe("GuardLogPanel", () => {
  it("lists the newest entries and filters by kind and by incident", async () => {
    const fetchMock = stubBackend(
      json([entry({ id: 2, kind: "redacted", detail: "1 secret(s) redacted" }), entry()]),
      json([entry({ id: 2, kind: "redacted", detail: "1 secret(s) redacted" })]),
      json([]),
    );
    render(<GuardLogPanel />);

    const list = await screen.findByRole("list", { name: "Guard log" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/dev/guard-log");
    const [first] = within(list).getAllByRole("listitem");
    expect(first).toHaveTextContent("Redacted");
    expect(first).toHaveTextContent("1 secret(s) redacted");

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "redacted");
    await waitFor(() =>
      expect(fetchMock.mock.calls[1][0]).toBe("/api/dev/guard-log?kind=redacted"),
    );

    await userEvent.type(screen.getByLabelText("Incident"), " inc_2 ");
    await userEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls[2][0]).toBe("/api/dev/guard-log?kind=redacted&incident_id=inc_2"),
    );
    expect(
      await screen.findByText("Nothing in the guard log for this filter."),
    ).toBeInTheDocument();
  });

  it("filters by an entry's incident when its id is clicked, and clears it again", async () => {
    const fetchMock = stubBackend(json([entry()]), json([entry()]), json([entry()]));
    render(<GuardLogPanel />);

    await userEvent.click(await screen.findByRole("button", { name: "inc_1" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls[1][0]).toBe("/api/dev/guard-log?incident_id=inc_1"),
    );
    expect(screen.getByLabelText("Incident")).toHaveValue("inc_1");

    await userEvent.click(screen.getByRole("button", { name: "Every incident" }));
    await waitFor(() => expect(fetchMock.mock.calls[2][0]).toBe("/api/dev/guard-log"));
  });
});
