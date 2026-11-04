import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppNav } from "@/components/app-nav";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

describe("AppNav", () => {
  it("links Home, Incidents and Settings, and not the developer panel", () => {
    render(<AppNav />);
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["Home", "Incidents", "Settings"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/",
      "/incidents",
      "/settings",
    ]);
  });

  it("marks the page you are on, Incidents for an incident's own page too", () => {
    pathname.current = "/incidents/abc";
    render(<AppNav />);
    expect(screen.getByRole("link", { name: "Incidents" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  });
});
