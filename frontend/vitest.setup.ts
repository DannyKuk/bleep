import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { setLanguage } from "@/lib/i18n";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  // A test that switched the dashboard to German leaves the next one in English.
  if (typeof document !== "undefined") setLanguage("en");
});
