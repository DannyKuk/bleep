export const LANGUAGE_KEY = "bleep:language";

/** Runs inline in <head> before first paint: puts the stored language on <html lang>, so a
 * screen reader and the browser's hyphenation use it from the start. The words themselves still
 * follow after hydration, in English first. */
export const LANGUAGE_SCRIPT = `(() => {
  try {
    const stored = localStorage.getItem(${JSON.stringify(LANGUAGE_KEY)});
    if (stored === "en" || stored === "de") document.documentElement.lang = stored;
  } catch {}
})();`;
