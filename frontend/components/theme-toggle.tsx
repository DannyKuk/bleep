"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { applyTheme, readTheme, storeTheme, THEMES, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const ICONS = { system: Monitor, light: Sun, dark: Moon } as const;

/** System, light or dark. The pick is kept in localStorage; the inline script in the root layout
 * applies it before first paint. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    // localStorage is only readable in the browser, after hydration
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(readTheme());
  }, []);

  useEffect(() => {
    if (theme !== "system") return;
    const query = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [theme]);

  function pick(next: Theme) {
    setTheme(next);
    storeTheme(next);
    applyTheme(next);
  }

  return (
    <div
      role="group"
      aria-label="Theme"
      className="inline-flex rounded-md border border-border p-0.5"
    >
      {THEMES.map((option) => {
        const Icon = ICONS[option];
        return (
          <button
            key={option}
            type="button"
            title={option[0].toUpperCase() + option.slice(1)}
            aria-label={option[0].toUpperCase() + option.slice(1)}
            aria-pressed={option === theme}
            onClick={() => pick(option)}
            className={cn(
              "rounded-sm p-1.5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
              option === theme
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}
