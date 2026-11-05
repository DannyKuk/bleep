import { describe, expect, it } from "vitest";

import { diagnosis, incident } from "@/components/incident/fixtures";
import { host, service } from "@/components/overview/fixtures";
import { heroSituation, isHurting, reachLine } from "@/components/overview/situation";
import type { IncidentDetail } from "@/lib/api-types";

const NOW = Date.parse("2026-11-02T19:00:00Z");

function detail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    incident: incident(),
    diagnosis: diagnosis(),
    proposal: null,
    approval: null,
    outcome: null,
    report: null,
    feedback: [],
    expectation: null,
    ...overrides,
  };
}

describe("heroSituation", () => {
  it("puts a plan that waits for the human ahead of everything else", () => {
    const waiting = incident({ id: "wait", status: "awaiting_approval" });
    const situation = heroSituation({
      incidents: [incident({ id: "busy", status: "investigating" }), waiting],
      hosts: [host({ reachable: false, health: "down" })],
      details: {},
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "incident", incident: { id: "wait" } });
  });

  it("prefers a waiting plan to a failed run, and counts the other one", () => {
    const situation = heroSituation({
      incidents: [
        incident({ id: "failed", status: "failed", closed_at: "2026-11-02T18:30:00Z" }),
        incident({ id: "wait", status: "awaiting_approval" }),
      ],
      hosts: [host()],
      details: {},
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "incident", incident: { id: "wait" }, others: 1 });
  });

  it("puts a sure plan ahead of an unsure one", () => {
    const situation = heroSituation({
      incidents: [
        incident({ id: "unsure", status: "awaiting_approval" }),
        incident({ id: "sure", status: "awaiting_approval" }),
      ],
      hosts: [host()],
      details: { unsure: detail({ diagnosis: diagnosis({ unsure: true }) }) },
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "incident", incident: { id: "sure" }, others: 1 });
  });

  it("forgets a failed run after a day", () => {
    const situation = heroSituation({
      incidents: [incident({ status: "failed", closed_at: "2026-10-30T18:00:00Z" })],
      hosts: [host()],
      details: {},
      now: NOW,
    });
    expect(situation).toEqual({ kind: "quiet" });
  });

  it("makes an unreachable host the headline before an incident Bleep works on", () => {
    const situation = heroSituation({
      incidents: [incident({ status: "investigating" })],
      hosts: [host({ id: "a" }), host({ id: "b", reachable: false, health: "down" })],
      details: {},
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "unreachable", host: { id: "b" } });
  });

  it("links an unreachable host to the incident about it", () => {
    const about = incident({
      id: "host-inc",
      status: "investigating",
      subject: { kind: "host", host_id: "b", service_id: null },
    });
    const situation = heroSituation({
      incidents: [about],
      hosts: [host({ id: "b", reachable: false, health: "down" })],
      details: {},
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "unreachable", incident: { id: "host-inc" } });
  });

  it("shows the incident Bleep works on when nothing needs the human", () => {
    const situation = heroSituation({
      incidents: [incident({ id: "busy", status: "paused" })],
      hosts: [host()],
      details: {},
      now: NOW,
    });
    expect(situation).toMatchObject({ kind: "incident", incident: { id: "busy" }, others: 0 });
  });

  it("is quiet when nothing is open, and says when there are no hosts", () => {
    const done = [incident({ status: "resolved" }), incident({ status: "closed" })];
    expect(heroSituation({ incidents: done, hosts: [host()], details: {}, now: NOW })).toEqual({
      kind: "quiet",
    });
    expect(heroSituation({ incidents: done, hosts: [], details: {}, now: NOW })).toEqual({
      kind: "no_hosts",
    });
  });
});

describe("quiet subjects", () => {
  const SERVICE_ID = "demo/compose:shop";
  const leftAlone = { scope: "service", mode: "leave_alone", until: null, since: "x" } as const;

  it("leaves a failed incident of a quiet service out of the hero", () => {
    const situation = heroSituation({
      incidents: [incident({ status: "failed", closed_at: "2026-11-02T18:30:00Z" })],
      hosts: [host({ quiet_service_ids: [SERVICE_ID] })],
      details: {},
      now: NOW,
    });
    expect(situation).toEqual({ kind: "quiet" });
  });

  it("does not make an asleep host the headline or count it as hurting", () => {
    const asleep = host({ reachable: false, may_sleep: true, health: "asleep" });
    const situation = heroSituation({ incidents: [], hosts: [asleep], details: {}, now: NOW });
    expect(situation).toEqual({ kind: "quiet" });
    expect(isHurting(asleep, [])).toBe(false);
    expect(reachLine([asleep], [])).toBe("It is fine.");
  });

  it("does not make an unreachable quiet host the headline", () => {
    const quiet = { ...leftAlone, scope: "host" } as const;
    const situation = heroSituation({
      incidents: [],
      hosts: [host({ reachable: false, health: "down", quiet })],
      details: {},
      now: NOW,
    });
    expect(situation).toEqual({ kind: "quiet" });
  });

  it("is not hurting when its only hurting service is quiet, and the reach line says fine", () => {
    const services = [service({ health: "down", quiet: leftAlone })];
    expect(isHurting(host({ health: "down" }), services)).toBe(false);
    expect(reachLine([host({ health: "down" })], services)).toBe("It is fine.");
  });

  it("still counts a service that is not quiet next to a quiet one", () => {
    const services = [
      service({ health: "down", quiet: leftAlone }),
      service({ id: "demo/compose:blog", name: "blog", health: "down" }),
    ];
    const line = reachLine([host({ health: "down" })], services);
    expect(line).toBe("Demo stack is hurting: 1 of its 2 services.");
  });

  it("a quiet host is not hurting", () => {
    const quiet = { ...leftAlone, scope: "host" } as const;
    const down = host({ reachable: false, health: "down", quiet });
    expect(isHurting(down, [])).toBe(false);
  });
});

describe("reachLine", () => {
  const three = (overrides: Parameters<typeof host>[0] = {}) => [
    host({ id: "demo", name: "Demo stack", ...overrides }),
    host({ id: "main", name: "coolify-main" }),
    host({ id: "sites", name: "coolify-sites" }),
  ];

  it("says one host is hurting, how many of its services, and that the rest are fine", () => {
    const services = [
      service({ id: "demo/a", host_id: "demo", health: "down" }),
      service({ id: "demo/b", host_id: "demo", health: "ok" }),
      service({ id: "demo/c", host_id: "demo", health: "stopped" }),
      service({ id: "main/x", host_id: "main", health: "down" }),
    ];
    expect(reachLine(three({ health: "down" }), services)).toBe(
      "Only Demo stack is hurting: 1 of its 3 services. The other two are fine.",
    );
  });

  it("says when a whole host is out of reach", () => {
    expect(reachLine(three({ reachable: false, health: "down" }), [])).toBe(
      "Demo stack is out of reach as a whole. The other two are fine.",
    );
  });

  it("names every host that is hurting", () => {
    const hosts = [
      host({ id: "a", name: "A", health: "degraded" }),
      host({ id: "b", name: "B", reachable: false, health: "unknown" }),
      host({ id: "c", name: "C" }),
    ];
    expect(reachLine(hosts, [])).toBe("A and B are hurting. The other one is fine.");
  });

  it("covers all of a host's services, and its only one", () => {
    const one = [host({ health: "down" })];
    expect(reachLine(one, [service({ health: "down" })])).toBe(
      "Demo stack is hurting: its only service.",
    );
    expect(
      reachLine(one, [
        service({ id: "demo/a", health: "down" }),
        service({ id: "demo/b", health: "degraded" }),
      ]),
    ).toBe("Demo stack is hurting: all 2 of its services.");
  });

  it("says all is fine in as few words as fit", () => {
    expect(reachLine([host()], [])).toBe("It is fine.");
    expect(reachLine([host({ id: "a" }), host({ id: "b" })], [])).toBe("Both are fine.");
    expect(reachLine(three(), [])).toBe("All three are fine.");
    expect(reachLine(three({ health: "unknown" }), [])).toBe("All three are fine.");
  });
});
