"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { applyTheme, readTheme, storeTheme, THEMES, type Theme } from "@/lib/theme";

const ICONS = { system: Monitor, light: Sun, dark: Moon } as const;
const NAMES = { system: "System", light: "Light", dark: "Dark" } as const;

export function nextTheme(theme: Theme): Theme {
  return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
}

/** One round button that steps through system, light and dark. The pick is kept in localStorage;
 * the inline script in the root layout applies it before first paint. */
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

  function step() {
    const next = nextTheme(theme);
    setTheme(next);
    storeTheme(next);
    applyTheme(next);
  }

  const Icon = ICONS[theme];
  const label = `Theme: ${NAMES[theme]}. Switch to ${NAMES[nextTheme(theme)]}`;
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={step}
      className="inline-flex size-11 items-center justify-center rounded-full border border-line bg-surface text-ink-2 transition-colors hover:bg-soft hover:text-ink focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <Icon aria-hidden className="size-[18px]" />
    </button>
  );
}
