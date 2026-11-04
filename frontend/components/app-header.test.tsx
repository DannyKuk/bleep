import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppHeader } from "@/components/app-header";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

describe("AppHeader", () => {
  beforeEach(() => {
    pathname = "/";
  });

  it("hides itself below the desktop breakpoint on an incident's own page", () => {
    pathname = "/incidents/inc-1";
    render(<AppHeader>nav</AppHeader>);
    expect(screen.getByRole("banner")).toHaveClass("max-lg:hidden", "print:hidden");
  });

  it.each(["/", "/incidents", "/settings", "/services/demo%2Fcompose%3Ashop"])(
    "stays on %s at every width",
    (path) => {
      pathname = path;
      render(<AppHeader>nav</AppHeader>);
      expect(screen.getByRole("banner")).not.toHaveClass("max-lg:hidden");
      expect(screen.getByRole("banner")).toHaveClass("print:hidden");
    },
  );
});
