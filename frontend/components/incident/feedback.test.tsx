import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FeedbackBar } from "@/components/incident/feedback";

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

function sent(fetchMock: ReturnType<typeof reply>): unknown {
  return JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
}

const stored = (vote: string, note: string | null = null, target = "diagnosis") => ({
  target,
  vote,
  note,
  at: "2026-11-02T18:10:00Z",
});

describe("FeedbackBar", () => {
  it("sends a thumbs down on the diagnosis and shows it as chosen", async () => {
    const fetchMock = reply(200, stored("down"));
    const onSaved = vi.fn();
    render(<FeedbackBar incidentId="inc-1" target="diagnosis" onSaved={onSaved} />);

    expect(screen.getByText("Was this diagnosis right?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wrong" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/incidents/inc-1/feedback",
      expect.objectContaining({ method: "POST" }),
    );
    expect(sent(fetchMock)).toEqual({ target: "diagnosis", vote: "down" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("status")).toHaveTextContent(/Marked wrong\. Bleep remembers this/);
    expect(onSaved).toHaveBeenCalled();
  });

  it("sends the note with the vote picked after it", async () => {
    const fetchMock = reply(200, stored("up", "redeploy was right", "proposal"));
    render(<FeedbackBar incidentId="inc-1" target="proposal" />);

    expect(screen.getByText("Was this plan right?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a note" }));
    await userEvent.type(screen.getByRole("textbox"), "  redeploy was right ");
    await userEvent.click(screen.getByRole("button", { name: "Right" }));

    expect(sent(fetchMock)).toEqual({ target: "proposal", vote: "up", note: "redeploy was right" });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Marked right: “redeploy was right”",
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows the vote the backend already has", () => {
    render(<FeedbackBar incidentId="inc-1" target="diagnosis" current={stored("up") as never} />);

    expect(screen.getByRole("button", { name: "Right" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Marked right.");
    expect(screen.getByRole("button", { name: "Right" })).toHaveClass("bg-ok-bg");
    expect(screen.getByRole("button", { name: "Wrong" })).not.toHaveClass("bg-down-bg");
  });

  it("tints a pressed Wrong red", () => {
    render(<FeedbackBar incidentId="inc-1" target="diagnosis" current={stored("down") as never} />);

    expect(screen.getByRole("button", { name: "Wrong" })).toHaveClass("bg-down-bg");
  });

  it("says when the vote could not be saved and lets the human try again", async () => {
    reply(409, { detail: "There is no plan to rate yet" });
    render(<FeedbackBar incidentId="inc-1" target="proposal" />);

    await userEvent.click(screen.getByRole("button", { name: "Right" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not save the vote: There is no plan to rate yet",
    );
    expect(screen.getByRole("button", { name: "Right" })).toBeEnabled();
  });
});
