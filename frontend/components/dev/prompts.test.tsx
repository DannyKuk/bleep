import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, prompt, sentBody, stubBackend } from "@/components/dev/fixtures";
import { PROMPT_READ_AGAIN, PromptsPanel } from "@/components/dev/prompts";

const RECOVERY = "/api/dev/prompts/watcher/recovery_line";
const PROPOSE = prompt({
  name: "graph/propose_unsure",
  placeholders: [],
  file_text: "Be careful.",
});

describe("PromptsPanel", () => {
  it("shows the first prompt's file text and placeholders, and switches prompts", async () => {
    const fetchMock = stubBackend(json([prompt(), PROPOSE]));
    render(<PromptsPanel />);

    const text = await screen.findByRole("textbox", { name: "Text of watcher/recovery_line" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/dev/prompts");
    expect(text).toHaveValue("Why did it stop?\n\n{logs}");
    expect(screen.getByLabelText("Placeholders")).toHaveTextContent("{logs}");
    expect(screen.getByText("prompts/watcher/recovery_line.md")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to the file" })).not.toBeInTheDocument();

    await userEvent.selectOptions(
      screen.getByLabelText(/Prompt \(0 of 2 overridden\)/),
      "graph/propose_unsure",
    );
    expect(screen.getByRole("textbox", { name: "Text of graph/propose_unsure" })).toHaveValue(
      "Be careful.",
    );
    expect(screen.getByLabelText("Placeholders")).toHaveTextContent("No placeholders");
  });

  it("saves an override and then offers to reset it to the file", async () => {
    const text = "Say why it stopped: {logs}";
    const fetchMock = stubBackend(
      json([prompt()]),
      json(prompt({ override: text, overridden_at: "2026-11-03T10:00:00Z", version: "p2" })),
      json(prompt()),
    );
    render(<PromptsPanel />);
    const box = await screen.findByRole("textbox", { name: /Text of/ });
    const save = screen.getByRole("button", { name: "Save the override" });
    expect(save).toBeDisabled();

    await userEvent.clear(box);
    await userEvent.type(box, "Say why it stopped: {{logs}");
    await userEvent.click(save);

    expect(fetchMock.mock.calls[1][0]).toBe(RECOVERY);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "PUT" });
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ "if-match": '"p1"' });
    expect(sentBody(fetchMock, 1)).toEqual({ text });
    expect(await screen.findByText(/Overridden/)).toBeInTheDocument();
    expect(screen.getByLabelText(/1 of 1 overridden/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reset to the file" }));
    expect(fetchMock.mock.calls[2][0]).toBe(RECOVERY);
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "DELETE" });
    expect(fetchMock.mock.calls[2][1]?.headers).toMatchObject({ "if-match": '"p2"' });
    await waitFor(() => expect(box).toHaveValue("Why did it stop?\n\n{logs}"));
    expect(screen.queryByRole("button", { name: "Reset to the file" })).not.toBeInTheDocument();
  });

  it("keeps the edit and shows the loader's reason when the override is refused", async () => {
    stubBackend(
      json([prompt()]),
      json({ detail: "use exactly the file's placeholders; missing: {logs}; unknown: none" }, 422),
    );
    render(<PromptsPanel />);
    const box = await screen.findByRole("textbox", { name: /Text of/ });

    await userEvent.clear(box);
    await userEvent.type(box, "No logs here");
    await userEvent.click(screen.getByRole("button", { name: "Save the override" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not save the override: use exactly the file's placeholders; missing: {logs}",
    );
    expect(box).toHaveValue("No logs here");
  });

  it("after a 409 reads the prompts again, keeps the edit on top and never retries", async () => {
    const elsewhere = prompt({
      override: "Changed elsewhere: {logs}",
      overridden_at: "2026-11-03T11:00:00Z",
      version: "p2",
    });
    const fetchMock = stubBackend(
      json([prompt()]),
      json({ detail: "the prompt changed since this version was read", version: "p2" }, 409),
      json([elsewhere]),
    );
    render(<PromptsPanel />);
    const box = await screen.findByRole("textbox", { name: /Text of/ });

    await userEvent.clear(box);
    await userEvent.type(box, "Mine: {{logs}");
    await userEvent.click(screen.getByRole("button", { name: "Save the override" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(PROMPT_READ_AGAIN);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2][0]).toBe("/api/dev/prompts");
    expect(box).toHaveValue("Mine: {logs}");
    expect(screen.getByText(/Overridden/)).toBeInTheDocument(); // the state read again
    expect(screen.getByRole("button", { name: "Save the override" })).toBeEnabled();
  });

  it("after a 409 on a reset shows the text in force now, since the person edited nothing", async () => {
    const mine = prompt({ override: "Mine: {logs}", overridden_at: "2026-11-03T10:00:00Z" });
    const elsewhere = prompt({ override: "Theirs: {logs}", version: "p2" });
    stubBackend(
      json([mine]),
      json({ detail: "the prompt changed since this version was read", version: "p2" }, 409),
      json([elsewhere]),
    );
    render(<PromptsPanel />);
    const box = await screen.findByRole("textbox", { name: /Text of/ });
    expect(box).toHaveValue("Mine: {logs}");

    await userEvent.click(screen.getByRole("button", { name: "Reset to the file" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: this prompt changed");
    await waitFor(() => expect(box).toHaveValue("Theirs: {logs}"));
  });

  it("says so when reading the prompts again after a 409 failed", async () => {
    stubBackend(
      json([prompt()]),
      json({ detail: "the prompt changed since this version was read", version: "p2" }, 409),
      json({ detail: "Bad Gateway" }, 502),
    );
    render(<PromptsPanel />);
    const box = await screen.findByRole("textbox", { name: /Text of/ });

    await userEvent.type(box, " more");
    await userEvent.click(screen.getByRole("button", { name: "Save the override" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Reading it again failed (Bad Gateway)");
    expect(alert).not.toHaveTextContent("has been read again");
  });
});
