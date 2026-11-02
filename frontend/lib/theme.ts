export const THEME_KEY = "bleep:theme";
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

/**
 * Runs inline in <head> before first paint: puts `dark` on <html> from the stored pick (or the
 * system setting), so a reader who chose dark never sees a light flash on load.
 */
export const THEME_SCRIPT = `(() => {
  let picked = null;
  try { picked = localStorage.getItem(${JSON.stringify(THEME_KEY)}); } catch {}
  const dark = picked === "dark" || (picked !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
})();`;

export function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function storeTheme(theme: Theme) {
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    // private mode: the pick lasts until reload
  }
}
