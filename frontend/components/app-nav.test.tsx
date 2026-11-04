import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppNav } from "@/components/app-nav";
import { json, stubBackend } from "@/components/dev/fixtures";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

describe("AppNav", () => {
  it("hides Dev while the backend has the dev panel off", async () => {
    const fetchMock = stubBackend(json({ detail: "Not Found" }, 404));
    render(<AppNav />);
    expect(await screen.findByRole("link", { name: "Settings" })).toBeInTheDocument();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dev", expect.anything()));
    expect(screen.queryByRole("link", { name: "Dev" })).not.toBeInTheDocument();
  });

  it("shows Dev when the dev panel is on", async () => {
    stubBackend(json({ enabled: true }));
    render(<AppNav />);
    expect(await screen.findByRole("link", { name: "Dev" })).toHaveAttribute("href", "/dev");
  });
});
