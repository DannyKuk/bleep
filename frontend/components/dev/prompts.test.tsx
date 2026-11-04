import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, prompt, sentBody, stubBackend } from "@/components/dev/fixtures";
import { PromptsPanel } from "@/components/dev/prompts";

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
      json(prompt({ override: text, overridden_at: "2026-11-03T10:00:00Z" })),
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
    expect(sentBody(fetchMock, 1)).toEqual({ text });
    expect(await screen.findByText(/Overridden/)).toBeInTheDocument();
    expect(screen.getByLabelText(/1 of 1 overridden/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reset to the file" }));
    expect(fetchMock.mock.calls[2][0]).toBe(RECOVERY);
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "DELETE" });
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
});
