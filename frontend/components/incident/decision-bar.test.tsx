import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DecisionBar } from "@/components/incident/decision-bar";
import { approval, proposal } from "@/components/incident/fixtures";

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
    screen.getByRole("button", { name: "Yes, go ahead" }),
    screen.getByRole("button", { name: "No, leave it" }),
    screen.getByRole("button", { name: "Tell Bleep something" }),
  ];
}

const refusedApproval = () =>
  approval({
    proposal: proposal({ kind: "no_action", steps: [], refused: true, reason: "refused" }),
  });

describe("DecisionBar", () => {
  it("lets the decision be made by keyboard alone, with a focus ring on each button", async () => {
    const fetchMock = reply(200, decided("approve", "dashboard"));
    const onDecided = vi.fn();
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={onDecided} />);

    for (const button of buttons()) expect(button).toHaveClass("focus-visible:ring-2");
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Yes, go ahead" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "approve" });
  });

  it("reaches No and Tell Bleep something by Tab, and refuses by keyboard", async () => {
    const fetchMock = reply(200, decided("reject", "dashboard"));
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={vi.fn()} />);

    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "No, leave it" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Tell Bleep something" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    await userEvent.keyboard(" ");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "reject" });
  });

  it("offers a refused plan an instruction and Let it go, never Yes, go ahead", async () => {
    const fetchMock = reply(200, decided("instruct", "dashboard", "restart shop-proxy-1"));
    render(<DecisionBar incidentId="inc-1" approval={refusedApproval()} onDecided={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Yes, go ahead" })).toBeNull();
    expect(screen.queryByRole("button", { name: "No, leave it" })).toBeNull();
    expect(screen.getByRole("button", { name: "Let it go" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Tell Bleep something" }));
    await userEvent.type(screen.getByRole("textbox"), "restart shop-proxy-1");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(sent(fetchMock)).toEqual({
      approval_id: "appr-1",
      kind: "instruct",
      text: "restart shop-proxy-1",
    });
  });

  it("offers no yes for a refused plan stored before the refused flag", () => {
    const reason = "Bleep's own checks refused the proposed plan twice, so nothing will run.";
    const legacy = approval({
      proposal: proposal({ kind: "no_action", steps: [], refused: undefined, reason }),
    });
    render(<DecisionBar incidentId="inc-1" approval={legacy} onDecided={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Yes, go ahead" })).toBeNull();
    expect(screen.getByRole("button", { name: "Let it go" })).toBeEnabled();
  });

  it("says a refused plan was let go, not rejected", async () => {
    reply(200, decided("reject", "dashboard"));
    render(<DecisionBar incidentId="inc-1" approval={refusedApproval()} onDecided={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Let it go" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/You let it go on the dashboard/),
    );
  });

  it("sends a reject when a refused plan is let go", async () => {
    const fetchMock = reply(200, decided("reject", "dashboard"));
    render(<DecisionBar incidentId="inc-1" approval={refusedApproval()} onDecided={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Let it go" }));

    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "reject" });
  });

  it("sends the approval it shows and locks after the decision", async () => {
    const fetchMock = reply(200, decided("approve", "dashboard"));
    const onDecided = vi.fn();
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: "Yes, go ahead" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/incidents/inc-1/decision",
      expect.objectContaining({ method: "POST" }),
    );
    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "approve" });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/You said yes on the dashboard/),
    );
    for (const button of buttons()) expect(button).toBeDisabled();
    expect(onDecided).toHaveBeenCalled();
  });

  it("shows the 409 message naming who decided, and locks", async () => {
    reply(409, { detail: "Already decided on Telegram", decided_via: "telegram" });
    const onDecided = vi.fn();
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: "No, leave it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already decided on Telegram");
    for (const button of buttons()) expect(button).toBeDisabled();
    expect(onDecided).toHaveBeenCalled();
  });

  it("keeps the buttons on another error, so the human can try again", async () => {
    reply(500, { detail: "Internal Server Error" });
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Yes, go ahead" }));

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

  it("caps an instruction at 2,000 characters", async () => {
    render(<DecisionBar incidentId="inc-1" approval={approval()} onDecided={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Tell Bleep something" }));
    expect(screen.getByRole("textbox")).toHaveAttribute("maxlength", "2000");
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
    expect(screen.getByRole("status")).toHaveTextContent(/You said no on Telegram/);
    for (const button of buttons()) expect(button).toBeDisabled();
  });
});

describe("DecisionBar pinned to a phone screen", () => {
  const pinned = (props: Partial<Parameters<typeof DecisionBar>[0]> = {}) =>
    render(
      <DecisionBar
        incidentId="inc-1"
        approval={approval()}
        onDecided={vi.fn()}
        pinned
        {...props}
      />,
    );
  const bar = (container: HTMLElement) => container.querySelector("[data-pinned]") as HTMLElement;

  it("sits at the bottom of the screen, yes full width at 56 px, the other two side by side", () => {
    const { container } = pinned();
    expect(bar(container)).toHaveClass("fixed", "inset-x-0", "bottom-0");
    expect(bar(container)).toHaveClass(
      "pb-[max(env(safe-area-inset-bottom),16px)]",
      "pl-[max(env(safe-area-inset-left),16px)]",
      "pr-[max(env(safe-area-inset-right),16px)]",
    );
    expect(screen.getByRole("region", { name: "Your answer" })).toBe(bar(container));
    const [yes, no, tell] = buttons();
    expect(yes).toHaveClass("h-14", "w-full");
    expect(no).toHaveClass("h-12", "flex-1");
    expect(tell).toHaveClass("h-12", "flex-1");
    expect(no.parentElement).toBe(tell.parentElement);
    expect(tell).toHaveTextContent(/^Tell Bleep$/);
  });

  it("sends the approval it shows, then becomes one status line", async () => {
    const fetchMock = reply(200, decided("approve", "dashboard"));
    const onDecided = vi.fn();
    const { container, rerender } = pinned({ onDecided });

    await userEvent.click(screen.getByRole("button", { name: "Yes, go ahead" }));

    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "approve" });
    expect(await screen.findByRole("status")).toHaveTextContent(/You said yes on the dashboard/);
    expect(within(bar(container)).queryAllByRole("button")).toHaveLength(0);
    // The tapped button is gone; focus is on the line that replaced it, not lost to the page.
    expect(bar(container)).toContainElement(document.activeElement as HTMLElement);
    expect(document.activeElement).toContainElement(screen.getByRole("status"));
    expect(onDecided).toHaveBeenCalled();

    const answered = approval({
      status: "approved",
      decision: decided("approve", "dashboard") as never,
    });
    for (const [status, line] of [
      ["executing", "Running the plan."],
      ["verifying", "Checking it worked."],
      ["resolved", "Fixed and checked."],
    ] as const) {
      rerender(
        <DecisionBar
          incidentId="inc-1"
          approval={answered}
          onDecided={onDecided}
          pinned
          status={status}
        />,
      );
      expect(screen.getByRole("status")).toHaveTextContent(line);
    }
  });

  it("says a no ran nothing, or only that this plan did not run when an earlier one did", () => {
    const rejected = approval({
      status: "rejected",
      decision: decided("reject", "telegram") as never,
    });
    const { rerender } = pinned({ approval: rejected, nothingRanBefore: true });
    expect(screen.getByRole("status")).toHaveTextContent(
      /You said no on Telegram, .*\. Nothing ran\./,
    );
    expect(screen.queryByRole("button")).toBeNull();

    // Not known to be true (an earlier plan ran, or the history has not loaded): the weaker claim.
    rerender(<DecisionBar incidentId="inc-1" approval={rejected} onDecided={vi.fn()} pinned />);
    expect(screen.getByRole("status")).toHaveTextContent("This plan did not run.");
    expect(screen.getByRole("status")).not.toHaveTextContent("Nothing ran.");
  });

  it("shows the 409 message naming who decided, with no buttons left", async () => {
    reply(409, { detail: "Already decided on Telegram", decided_via: "telegram" });
    const onDecided = vi.fn();
    const { container } = pinned({ onDecided });

    await userEvent.click(screen.getByRole("button", { name: "No, leave it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already decided on Telegram");
    expect(within(bar(container)).queryAllByRole("button")).toHaveLength(0);
    expect(onDecided).toHaveBeenCalled();
  });

  it("keeps the buttons on another error, so the human can try again", async () => {
    reply(500, { detail: "Internal Server Error" });
    pinned();

    await userEvent.click(screen.getByRole("button", { name: "Yes, go ahead" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Internal Server Error/);
    for (const button of buttons()) expect(button).toBeEnabled();
  });

  it("opens the instruction inside the bar, capped at 2,000 characters", async () => {
    const fetchMock = reply(200, decided("instruct", "dashboard", "Restart only shop-api"));
    const { container } = pinned();

    await userEvent.click(screen.getByRole("button", { name: "Tell Bleep something" }));
    const box = within(bar(container)).getByRole("textbox");
    expect(box).toHaveAttribute("maxlength", "2000");
    expect(box).toHaveFocus();
    const send = within(bar(container)).getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Yes, go ahead" })).toBeNull();

    await userEvent.type(box, " Restart only shop-api ");
    await userEvent.click(send);

    expect(sent(fetchMock)).toEqual({
      approval_id: "appr-1",
      kind: "instruct",
      text: "Restart only shop-api",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(/Restart only shop-api/);
  });

  it("offers a refused plan only an instruction and Let it go", async () => {
    const fetchMock = reply(200, decided("reject", "dashboard"));
    pinned({ approval: refusedApproval() });

    expect(screen.queryByRole("button", { name: "Yes, go ahead" })).toBeNull();
    expect(screen.queryByRole("button", { name: "No, leave it" })).toBeNull();
    expect(screen.getByRole("button", { name: "Tell Bleep something" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Let it go" }));

    expect(sent(fetchMock)).toEqual({ approval_id: "appr-1", kind: "reject" });
    expect(await screen.findByRole("status")).toHaveTextContent(/You let it go on the dashboard/);
  });

  it("lifts itself above the on-screen keyboard, so Send stays in view", async () => {
    const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0 });
    vi.stubGlobal("visualViewport", viewport);
    vi.stubGlobal("innerHeight", 844);
    const { container } = pinned();
    expect(bar(container).style.bottom).toBe("0px");

    viewport.height = 500;
    await act(async () => void viewport.dispatchEvent(new Event("resize")));
    expect(bar(container).style.bottom).toBe("344px");
  });
});
