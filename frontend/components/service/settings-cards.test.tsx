import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  CheckedUrls,
  ExpectedExits,
  FollowedRules,
  ProtectedContainers,
  settingsKey,
  withService,
  type ServiceSettings,
} from "@/components/service/settings-cards";
import { sshHost } from "@/components/settings/fixtures";
import { setLanguage } from "@/lib/i18n";

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

describe("the service's settings cards in German", () => {
  const settings: ServiceSettings = {
    hostId: "m1",
    view: {
      urls: [{ url: "https://shop.example/", expect_status: 200, body_contains: null }],
      rules: ["Nachts nie neu starten"],
      protected_containers: [],
      hosts_file_protected_containers: ["shop-db-1"],
      exits: ["fetch"],
    },
    overrides: undefined,
    hostRules: ["Immer fragen"],
    save: vi.fn(async () => undefined),
  };

  it("names the checked URLs, rules, protected containers and jobs in German", async () => {
    await act(async () => setLanguage("de"));
    render(
      <>
        <CheckedUrls name="shop" statuses={[]} settings={settings} />
        <FollowedRules name="shop" rules={[]} hostRules={[]} settings={settings} />
        <ProtectedContainers name="shop" settings={settings} />
        <ExpectedExits name="shop" settings={settings} />
      </>,
    );
    expect(screen.getByText("Noch nicht geprüft")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "URLs bearbeiten" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Regeln: Dieser Dienst" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Regeln: Sein Host" })).toBeInTheDocument();
    expect(screen.getByText("Ich ändere nie etwas ohne dein Ja.")).toBeInTheDocument();
    const fixed = screen.getByRole("list", { name: "Durch die Hosts-Datei geschützt: shop" });
    expect(within(fixed).getByText("(aus der Hosts-Datei)")).toBeInTheDocument();
    expect(
      screen.getByText("Auf diese greife ich nie zu, auch nicht nach deinem Ja."),
    ).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Darf sich beenden: shop" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Geschützte Container bearbeiten" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jobs bearbeiten" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Regeln bearbeiten" }));
    expect(screen.getByLabelText("Regeln für shop")).toBeInTheDocument();
    expect(
      screen.getByText("Regel hinzufügen, zum Beispiel: die Datenbank tagsüber nie neu starten"),
    ).toBeInTheDocument();
  });
});
