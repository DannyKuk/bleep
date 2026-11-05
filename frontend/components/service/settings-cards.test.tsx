import { describe, expect, it } from "vitest";

import { settingsKey, withService } from "@/components/service/settings-cards";
import { sshHost } from "@/components/settings/fixtures";

describe("withService", () => {
  it("changes one field and keeps the service's and the host's others", () => {
    const host = {
      rules: ["host rule"],
      services: {
        "compose:shop": { rules: ["r"] },
        "compose:worker": { urls: [] },
      },
    };
    expect(withService(host, "compose:shop", { urls: [] })).toEqual({
      rules: ["host rule"],
      services: {
        "compose:shop": { rules: ["r"], urls: [] },
        "compose:worker": { urls: [] },
      },
    });
  });

  it("drops a field set to undefined and a service left with nothing", () => {
    const host = { services: { "compose:shop": { rules: ["r"] } } };
    expect(withService(host, "compose:shop", { rules: undefined })).toEqual({ services: {} });
    expect(withService(undefined, "compose:shop", { rules: ["new"] })).toEqual({
      services: { "compose:shop": { rules: ["new"] } },
    });
  });
});

describe("settingsKey", () => {
  const shop = sshHost().services["compose:shop"];

  it("takes the service's own key first, then its name, as discovery does", () => {
    const both = sshHost({ services: { "compose:shop": shop, shop } });
    expect(settingsKey(both, "compose:shop", "shop")).toBe("compose:shop");
    expect(settingsKey(sshHost({ services: { shop } }), "compose:shop", "shop")).toBe("shop");
  });

  it("finds nothing for a service the host's settings do not hold", () => {
    expect(settingsKey(sshHost(), "compose:worker", "worker")).toBeUndefined();
    expect(settingsKey(sshHost({ services: { shop } }), "compose:shop")).toBeUndefined();
  });
});
