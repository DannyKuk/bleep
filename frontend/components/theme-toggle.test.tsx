import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { nextTheme, ThemeToggle } from "@/components/theme-toggle";
import { THEME_KEY } from "@/lib/theme";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );
  });

  it("steps system, light, dark and back to system", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });

  it("is one button that applies and keeps the next theme", async () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Theme: System. Switch to Light" });
    await userEvent.click(button);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    await userEvent.click(screen.getByRole("button", { name: "Theme: Light. Switch to Dark" }));
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement).toHaveClass("dark");
    await userEvent.click(screen.getByRole("button", { name: "Theme: Dark. Switch to System" }));
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("starts from the stored pick", async () => {
    localStorage.setItem(THEME_KEY, "dark");
    render(<ThemeToggle />);
    expect(
      await screen.findByRole("button", { name: "Theme: Dark. Switch to System" }),
    ).toBeInTheDocument();
  });

  it("keeps two mounted toggles in agreement", async () => {
    render(
      <>
        <div data-testid="a">
          <ThemeToggle />
        </div>
        <div data-testid="b">
          <ThemeToggle />
        </div>
      </>,
    );
    await userEvent.click(within(screen.getByTestId("a")).getByRole("button"));
    expect(screen.getAllByRole("button", { name: "Theme: Light. Switch to Dark" })).toHaveLength(2);
    await userEvent.click(within(screen.getByTestId("b")).getByRole("button"));
    expect(screen.getAllByRole("button", { name: "Theme: Dark. Switch to System" })).toHaveLength(
      2,
    );
  });

  it("never lets a system change override an explicit pick, however many toggles exist", async () => {
    let onChange: () => void = () => {};
    let dark = false;
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        get matches() {
          return dark;
        },
        addEventListener: (_: string, fn: () => void) => (onChange = fn),
        removeEventListener: vi.fn(),
      })),
    );
    render(
      <>
        <ThemeToggle />
        <ThemeToggle />
      </>,
    );
    await userEvent.click(screen.getAllByRole("button")[0]);
    expect(document.documentElement).not.toHaveClass("dark");
    dark = true;
    onChange();
    expect(document.documentElement).not.toHaveClass("dark");

    await userEvent.click(screen.getAllByRole("button")[0]);
    await userEvent.click(screen.getAllByRole("button")[0]);
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    onChange();
    expect(document.documentElement).toHaveClass("dark");
  });
});
