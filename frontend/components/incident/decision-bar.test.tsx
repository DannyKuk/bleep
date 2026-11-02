import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DecisionBar } from "@/components/incident/decision-bar";
import { approval } from "@/components/incident/fixtures";

function reply(status: number, body: unknown) {
  const fetchMock = vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The JSON body of the first request. */
function sent(fetchMock: ReturnType<typeof reply>): unknown {
  return JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
}

const decided = (kind: string, via: string, text: string | null = null) => ({
  kind,
  text,
  via,
  at: "2026-11-02T18:05:00Z",
});

function buttons() {
  return [
    screen.getByRole("button", { name: "Approve" }),
    screen.getByRole("button", { name: "Reject" }),
    screen.getByRole("button", { name: "Tell Bleep something" }),
  ];
}

describe("DecisionBar", () => {
  it("sends the approval it shows and locks after the decision", async () => {
    const fetchMock = reply(200, decided("approve", "dashboard"));
    const onDecided = vi.fn();
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/incidents/inc-1/decision",
      expect.objectContaining({ method: "POST" }),
    );
    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "approve" });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/Approved on the dashboard/),
    );
    for (const button of buttons()) expect(button).toBeDisabled();
    expect(onDecided).toHaveBeenCalled();
  });

  it("shows the 409 message naming who decided, and locks", async () => {
    reply(409, { detail: "Already decided on Telegram", decided_via: "telegram" });
    const onDecided = vi.fn();
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already decided on Telegram");
    for (const button of buttons()) expect(button).toBeDisabled();
    expect(onDecided).toHaveBeenCalled();
  });

  it("keeps the buttons on another error, so the human can try again", async () => {
    reply(500, { detail: "Internal Server Error" });
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Internal Server Error/);
    for (const button of buttons()) expect(button).toBeEnabled();
  });

  it("sends an instruction with its text", async () => {
    const fetchMock = reply(200, decided("instruct", "dashboard", "Check the database first"));
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Tell Bleep something" }));
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "  Check the database first ");
    await userEvent.click(send);

    expect(sent(fetchMock)).toEqual({
      approval_id: "appr-1",
      kind: "instruct",
      text: "Check the database first",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/Check the database first/);
  });

  it("shows a decision made elsewhere with the buttons locked", () => {
    render(
      <DecisionBar
        incidentId="inc-1"
        approval={approval({
          status: "rejected",
          decision: decided("reject", "telegram") as never,
        })}
        onDecided={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/Rejected on Telegram/);
    for (const button of buttons()) expect(button).toBeDisabled();
  });
});
