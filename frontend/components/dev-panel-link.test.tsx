import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DevPanelLink } from "@/components/dev-panel-link";
import { json, stubBackend } from "@/components/dev/fixtures";

describe("DevPanelLink", () => {
  it("shows nothing while the backend has the dev panel off", async () => {
    const fetchMock = stubBackend(json({ detail: "Not Found" }, 404));
    render(<DevPanelLink />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dev", expect.anything()));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("links the developer panel when it is on", async () => {
    stubBackend(json({ enabled: true }));
    render(<DevPanelLink />);
    expect(await screen.findByRole("link", { name: "Developer panel" })).toHaveAttribute(
      "href",
      "/dev",
    );
  });
});
