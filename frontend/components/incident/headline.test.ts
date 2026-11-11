import { describe, expect, it } from "vitest";

import { approval, detail, diagnosis, incident, proposal } from "@/components/incident/fixtures";
import { headline, headlineText, lookedAt, subjectName } from "@/components/incident/headline";
import { en } from "@/lib/i18n/en";
import type { IncidentStatus, Outcome } from "@/lib/api-types";

const at = (status: IncidentStatus, outcome: Outcome | null = null) =>
  headlineText(headline(detail({ incident: incident({ status }), outcome }), en));

describe("subjectName", () => {
  it("names a Compose project or a container by its own name", () => {
    expect(subjectName(incident().subject, en)).toEqual({ lead: "", name: "shop" });
    expect(
      subjectName(
        { kind: "service", host_id: "demo", service_id: "demo/container:shop-web-1" },
        en,
      ),
    ).toEqual({ lead: "", name: "shop-web-1" });
  });

  it("names a host as a host, and an id it cannot read as this service", () => {
    expect(subjectName({ kind: "host", host_id: "demo", service_id: null }, en)).toEqual({
      lead: "Host ",
      name: "demo",
    });
    expect(
      subjectName({ kind: "service", host_id: "demo", service_id: "demo/id:4f1c" }, en),
    ).toEqual({
      lead: "This service",
      name: "",
    });
  });

  it("marks characters that draw nothing in a name from the host, and cuts a long one", () => {
    const hidden = "shop\u200b\u202e";
    expect(
      subjectName({ kind: "service", host_id: "demo", service_id: `demo/compose:${hidden}` }, en)
        .name,
    ).toBe("shop\uFFFD\uFFFD");
    const long = "x".repeat(80);
    const { name } = subjectName(
      {
        kind: "service",
        host_id: "demo",
        service_id: `demo/compose:${long}`,
      },
      en,
    );
    expect(name).toHaveLength(40);
    expect(name.endsWith("…")).toBe(true);
  });
});

describe("headline", () => {
  it("asks for a yes while a plan waits, with the subject's name apart as data", () => {
    expect(at("awaiting_approval")).toBe("shop is in trouble. Can I fix it?");
    expect(headline(detail(), en)).toEqual({
      lead: "",
      name: "shop",
      text: " is in trouble. Can I fix it?",
    });
  });

  it("says Bleep is not sure when the diagnosis is unsure", () => {
    const unsure = detail({ diagnosis: diagnosis({ unsure: true }) });
    expect(headlineText(headline(unsure, en))).toBe(
      "shop is in trouble. I have a plan, but I am not sure.",
    );
  });

  it("does not ask to fix anything when the waiting plan was refused", () => {
    const refused = detail({
      approval: approval({ proposal: proposal({ kind: "no_action", steps: [], refused: true }) }),
    });
    expect(headlineText(headline(refused, en))).toBe("I found no plan I may run. What now?");
  });

  it("follows the run", () => {
    expect(at("investigating")).toBe("I am looking into it.");
    expect(at("executing")).toBe("On it.");
    expect(at("verifying")).toBe("Almost there.");
    expect(at("resolved", "resolved")).toBe("Fixed and checked.");
  });

  it("says a look that found the service healthy has nothing to do, by the service's name", () => {
    const closed = (fields: Parameters<typeof incident>[0]) =>
      headline(
        detail({ incident: incident({ status: "closed", ...fields }), outcome: "healthy" }),
        en,
      );
    expect(closed({})).toEqual({ lead: "", name: "shop", text: " looks healthy. Nothing to do." });
    const coolify = {
      kind: "service" as const,
      host_id: "vps",
      service_id: "vps/vclhwoxxy60z4vk70g0vpesq",
    };
    expect(headlineText(closed({ subject: coolify, short_name: "dkwebsite #3" }))).toBe(
      "dkwebsite looks healthy. Nothing to do.",
    );
    expect(headlineText(closed({ subject: coolify, short_name: null }))).toBe(
      "This service looks healthy. Nothing to do.",
    );
    expect(headlineText(closed({}))).not.toMatch(/cannot fix|fixed/i);
  });

  it("names a looked-at service by its id's name first, then its short name", () => {
    const coolify = { kind: "service" as const, host_id: "vps", service_id: "vps/abc" };
    expect(lookedAt({ subject: coolify, short_name: "my​site #1" }, en)).toEqual({
      lead: "",
      name: "my�site",
    });
    expect(lookedAt(incident({ short_name: "other #2" }), en)).toEqual({ lead: "", name: "shop" });
  });

  it("never says fixed for a closed incident, whatever its outcome", () => {
    const outcomes: Outcome[] = [
      "healthy",
      "rejected",
      "no_action",
      "unverified",
      "budget",
      "replan_limit",
    ];
    for (const outcome of outcomes) expect(at("closed", outcome)).not.toMatch(/fixed/i);
    expect(at("closed", "rejected")).toBe("Okay, I left it as it is.");
    expect(at("closed", "unverified")).toBe("I ran the plan, but could not confirm the fix.");
    expect(at("closed")).toBe("Closed.");
  });

  it("says it waits for a host that does not answer, and promises no plan", () => {
    expect(at("waiting")).toBe("The host does not answer. I wait for it to come back.");
  });

  it("says a failed run needs a look", () => {
    expect(at("failed")).toBe("I got stuck. This needs a look.");
  });
});
