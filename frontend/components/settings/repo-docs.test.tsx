import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, repoDocs, stubBackend } from "@/components/settings/fixtures";
import { RepoDocs } from "@/components/settings/repo-docs";
import type { RepoDocsRefreshed } from "@/lib/knowledge-api";

const REFRESH = "POST /api/knowledge/repos/refresh";

function refreshed(overrides: Partial<RepoDocsRefreshed> = {}): RepoDocsRefreshed {
  return {
    ...repoDocs({
      last_read_at: "2026-11-13T12:00:00Z",
      repos: [
        { repo: "acme/shop", chunks: 14 },
        { repo: "acme/blog", chunks: 1 },
      ],
    }),
    read: 2,
    linked: 2,
    embedded: 3,
    ...overrides,
  };
}

describe("RepoDocs", () => {
  it("shows the last read time and the chunks of each repo before any press", () => {
    render(<RepoDocs initial={repoDocs()} />);

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
    render(<RepoDocs initial={repoDocs({ last_read_at: null, repos: [] })} />);

    expect(screen.getByText("Not yet")).toBeInTheDocument();
    expect(screen.getByText(/No service links a repo/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("reads the docs on a press and shows the new time, the chunks and what was read", async () => {
    const fetchMock = stubBackend({ [REFRESH]: json(refreshed()) });
    render(<RepoDocs initial={repoDocs()} />);

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Read 2 repos.");
    expect(screen.getByText(/2026/).closest("time")).toHaveAttribute(
      "datetime",
      "2026-11-13T12:00:00Z",
    );
    expect(screen.getByText("acme/shop").closest("li")).toHaveTextContent("14 chunks");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
  });

  it("is disabled while it reads, so a second press cannot start another", async () => {
    let finish: (response: Response) => void = () => {};
    const fetchMock = stubBackend({});
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => (finish = resolve)));
    render(<RepoDocs initial={repoDocs()} />);

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));
    const reading = screen.getByRole("button", { name: "Reading the docs" });
    expect(reading).toBeDisabled();
    await userEvent.click(reading);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    finish(json(refreshed()));
    expect(await screen.findByRole("button", { name: "Re-read docs" })).toBeEnabled();
  });

  it("says when only some of the repos could be read", async () => {
    stubBackend({ [REFRESH]: json(refreshed({ read: 1 })) });
    render(<RepoDocs initial={repoDocs()} />);

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Read 1 of 2 repos. The others could not be read",
    );
  });

  it("shows a failed read and keeps what it showed", async () => {
    stubBackend({ [REFRESH]: json({ detail: "the embedder is down" }, 500) });
    render(<RepoDocs initial={repoDocs()} />);

    await userEvent.click(screen.getByRole("button", { name: "Re-read docs" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not read the docs: the embedder is down",
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-read docs" })).toBeEnabled());
    expect(screen.getByText("acme/shop").closest("li")).toHaveTextContent("12 chunks");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
