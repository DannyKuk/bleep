import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, repoDocs, stubBackend } from "@/components/settings/fixtures";
import { RepoDocs } from "@/components/settings/repo-docs";
import type { RepoDocsResult } from "@/lib/knowledge-api";

const REFRESH = "POST /api/knowledge/repos/refresh";
const STATE = "GET /api/knowledge/repos";
const BOTH_READ: RepoDocsResult = { failed: false, read: 2, linked: 2, embedded: 3 };

const reading = () => json(repoDocs({ reading: true }), 202);
const done = (result: RepoDocsResult = BOTH_READ) =>
  json(
    repoDocs({
      last_read_at: "2026-11-13T12:00:00Z",
      repos: [
        { repo: "acme/shop", chunks: 14 },
        { repo: "acme/blog", chunks: 1 },
      ],
      result,
    }),
  );

function renderDocs(initial = repoDocs()) {
  return render(<RepoDocs initial={initial} pollMs={5} />);
}

describe("RepoDocs", () => {
  it("shows the last read time and the chunks of each repo before any press", () => {
    renderDocs();

    expect(screen.getByText(/2026/).closest("time")).toHaveAttribute(
      "datetime",
      "2026-11-13T09:30:00Z",
    );
    const repos = screen.getByRole("list", { name: "Linked repos" });
    expect(within(repos).getByText("acme/shop").closest("li")).toHaveTextContent("12 chunks");
    expect(within(repos).getByText("acme/blog").closest("li")).toHaveTextContent("1 chunk");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says the docs have not been read yet, and what to do when no repo is linked", () => {
    renderDocs(repoDocs({ last_read_at: null, repos: [] }));

    expect(screen.getByText("Not yet")).toBeInTheDocument();
    expect(screen.getByText(/No service links a repo/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("starts a read on a press, asks again while it runs, then shows the time, chunks and result", async () => {
    const fetchMock = stubBackend({
      [REFRESH]: reading(),
      [STATE]: [reading(), reading(), done()],
    });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(screen.getByRole("button", { name: "Reading the docs" })).toBeDisabled();
    expect(await screen.findByRole("status")).toHaveTextContent("Read 2 repos.");
    expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled();
    expect(screen.getByText(/2026/).closest("time")).toHaveAttribute(
      "datetime",
      "2026-11-13T12:00:00Z",
    );
    expect(screen.getByText("acme/shop").closest("li")).toHaveTextContent("14 chunks");
    const methods = fetchMock.mock.calls.map(([, init]) => init?.method ?? "GET");
    expect(methods).toEqual(["POST", "GET", "GET", "GET"]);
  });

  it("stays disabled while the backend reads, and cannot start a second read", async () => {
    const fetchMock = stubBackend({ [REFRESH]: reading(), [STATE]: [reading(), reading()] });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));
    const button = await screen.findByRole("button", { name: "Reading the docs" });
    await userEvent.click(button);

    expect(button).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("follows a read that was running when the page loaded, a daily one or another's press, and shows how it ended", async () => {
    stubBackend({ [STATE]: [done()] });
    renderDocs(repoDocs({ reading: true, result: { ...BOTH_READ, read: 1 } }));

    expect(screen.getByRole("button", { name: "Reading the docs" })).toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull(); // the earlier read's result waits for this one
    expect(await screen.findByRole("status")).toHaveTextContent("Read 2 repos.");
  });

  it("shows how the last read ended, whoever started it, before any press", () => {
    renderDocs(repoDocs({ result: { failed: true, read: 0, linked: 0, embedded: 0 } }));

    expect(screen.getByRole("alert")).toHaveTextContent("The docs could not be read.");
    expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled();
  });

  it("says when only some of the repos could be read", async () => {
    stubBackend({
      [REFRESH]: reading(),
      [STATE]: [done({ failed: false, read: 1, linked: 2, embedded: 0 })],
    });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Read 1 of 2 repos. The others could not be read",
    );
  });

  it("says a failed read failed, without the backend's reason", async () => {
    stubBackend({
      [REFRESH]: reading(),
      [STATE]: [done({ failed: true, read: 0, linked: 0, embedded: 0 })],
    });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The docs could not be read. Bleep's log says why.",
    );
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled();
  });

  it("shows a refused press as it really comes, a plain-text 500, and keeps what it showed", async () => {
    stubBackend({ [REFRESH]: new Response("Internal Server Error", { status: 500 }) });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not ask Bleep to read the docs: HTTP 500",
    );
    expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled();
    expect(screen.getByText("acme/shop").closest("li")).toHaveTextContent("12 chunks");
  });

  it("stops asking and says so when a poll fails", async () => {
    const fetchMock = stubBackend({
      [REFRESH]: reading(),
      [STATE]: [new Response("Bad Gateway", { status: 502 })],
    });
    renderDocs();

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 502");
    expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
