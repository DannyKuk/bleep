import { describe, expect, it } from "vitest";

import nextConfig from "@/next.config";

describe("next.config headers", () => {
  it("forbids framing on every path, so a framed dashboard cannot be clicked through (#626)", async () => {
    const rules = await nextConfig.headers!();

    expect(rules).toHaveLength(1);
    expect(rules[0].source).toBe("/:path*");
    expect(rules[0].headers).toEqual(
      expect.arrayContaining([
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
        { key: "X-Frame-Options", value: "DENY" },
      ]),
    );
  });
});
