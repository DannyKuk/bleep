import { describe, expect, it } from "vitest";

import { serviceHref, serviceIdFrom } from "@/lib/routes";

describe("service routes", () => {
  it("round-trips a service id through its page's path", () => {
    const href = serviceHref("demo/compose:shop");
    expect(href).toBe("/services/demo%2Fcompose%3Ashop");
    expect(serviceIdFrom(href.split("/")[2])).toBe("demo/compose:shop");
  });

  it("takes a segment the router already decoded, or one that cannot be decoded, as it is", () => {
    expect(serviceIdFrom("demo/compose:shop")).toBe("demo/compose:shop");
    expect(serviceIdFrom("100%")).toBe("100%");
  });
});
