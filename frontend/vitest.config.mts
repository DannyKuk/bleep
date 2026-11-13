import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Times are formatted in the machine's time zone; tests expect Berlin's, so a UTC CI runner
// formats them the same as the laptops they were written on (#483).
process.env.TZ = "Europe/Berlin";

export default defineConfig({
  plugins: [react()],
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
