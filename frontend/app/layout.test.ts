import { describe, expect, it, vi } from "vitest";

vi.mock("next/font/google", () => ({
  Nunito: () => ({ variable: "font-nunito" }),
  JetBrains_Mono: () => ({ variable: "font-jetbrains-mono" }),
}));

describe("the root layout", () => {
  it("covers the whole screen, so the safe-area insets have values on an iPhone", async () => {
    const { viewport } = await import("@/app/layout");
    expect(viewport).toMatchObject({ viewportFit: "cover" });
  });
});
