import { describe, expect, it } from "vitest";

import { approval, detail, diagnosis, incident, proposal } from "@/components/incident/fixtures";
import { headline, subjectName } from "@/components/incident/headline";
import type { IncidentStatus, Outcome } from "@/lib/api-types";

const at = (status: IncidentStatus, outcome: Outcome | null = null) =>
  headline(detail({ incident: incident({ status }), outcome }));

describe("subjectName", () => {
  it("names a Compose project or a container by its own name", () => {
    expect(subjectName(incident().subject)).toBe("shop");
    expect(
      subjectName({ kind: "service", host_id: "demo", service_id: "demo/container:shop-web-1" }),
    ).toBe("shop-web-1");
  });

  it("names a host as a host, and an id it cannot read as this service", () => {
    expect(subjectName({ kind: "host", host_id: "demo", service_id: null })).toBe("Host demo");
    expect(subjectName({ kind: "service", host_id: "demo", service_id: "demo/id:4f1c" })).toBe(
      "This service",
    );
  });
});

describe("headline", () => {
  it("asks for a yes while a plan waits", () => {
    expect(at("awaiting_approval")).toBe("shop is in trouble. Can I fix it?");
  });

  it("says Bleep is not sure when the diagnosis is unsure", () => {
    const unsure = detail({ diagnosis: diagnosis({ unsure: true }) });
    expect(headline(unsure)).toBe("shop is in trouble. I have a plan, but I am not sure.");
  });

  it("does not ask to fix anything when the waiting plan was refused", () => {
    const refused = detail({
      approval: approval({ proposal: proposal({ kind: "no_action", steps: [], refused: true }) }),
    });
    expect(headline(refused)).toBe("I found no plan I may run. What now?");
  });

  it("follows the run", () => {
    expect(at("investigating")).toBe("I am looking into it.");
    expect(at("executing")).toBe("On it.");
    expect(at("verifying")).toBe("Almost there.");
    expect(at("resolved", "resolved")).toBe("Fixed and checked.");
  });

  it("never says fixed for a closed incident, whatever its outcome", () => {
    const outcomes: Outcome[] = ["rejected", "no_action", "unverified", "budget", "replan_limit"];
    for (const outcome of outcomes) expect(at("closed", outcome)).not.toMatch(/fixed/i);
    expect(at("closed", "rejected")).toBe("Okay, I left it as it is.");
    expect(at("closed", "unverified")).toBe("I ran the plan, but could not confirm the fix.");
    expect(at("closed")).toBe("Closed.");
  });

  it("says a failed run needs a look", () => {
    expect(at("failed")).toBe("I got stuck. This needs a look.");
  });
});
