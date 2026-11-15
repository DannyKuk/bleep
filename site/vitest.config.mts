import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = (...parts: string[]) => path.join(import.meta.dirname, ...parts);

// The tsconfig paths point `react` at its types (for the dashboard files the page imports), which
// Vite cannot load, so the tests spell the same aliases out with the packages themselves.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^~\/(.*)$/, replacement: here("$1") },
      { find: /^@\/(.*)$/, replacement: here("..", "frontend", "$1") },
      { find: /^(clsx|tailwind-merge)$/, replacement: here("node_modules", "$1") },
    ],
    dedupe: ["react", "react-dom"],
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
