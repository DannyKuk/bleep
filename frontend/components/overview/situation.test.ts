import { describe, expect, it } from "vitest";

import { diagnosis, incident } from "@/components/incident/fixtures";
import { host, service } from "@/components/overview/fixtures";
import {
  heroSituation,
  isHurting,
  reachLine,
  watchedNames,
  watchReason,
} from "@/components/overview/situation";
import type { HostView, IncidentDetail, ServiceView } from "@/lib/api-types";
import { MESSAGES } from "@/lib/i18n";

const reach = (hosts: HostView[], services: ServiceView[]) =>
  reachLine(hosts, services, MESSAGES.en);

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
    repeats: 1,
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

describe("heroSituation while Bleep watches (#358)", () => {
  const down = (overrides: Partial<ServiceView> = {}) => service({ health: "down", ...overrides });
  const situation = (services: ServiceView[], hosts = [host()]) =>
    heroSituation({ incidents: [], hosts, services, details: {}, now: NOW });

  it("watches a service that is down with no incident open for it", () => {
    expect(situation([down(), service({ id: "b" })])).toMatchObject({
      kind: "watching",
      services: [{ id: "demo/compose:shop" }],
    });
  });

  it("puts an incident Bleep works on and an unreachable host first", () => {
    const working = heroSituation({
      incidents: [incident({ status: "investigating" })],
      hosts: [host()],
      services: [down({ id: "b" })],
      details: {},
      now: NOW,
    });
    expect(working).toMatchObject({ kind: "incident" });
    const unreachable = situation([down()], [host(), host({ id: "far", reachable: false })]);
    expect(unreachable).toMatchObject({ kind: "unreachable", host: { id: "far" } });
  });

  it("is quiet for a service down but quiet, one with its incident open, or one stopped", () => {
    const quiet = { scope: "service", mode: "snooze", until: null, since: "x" } as const;
    expect(
      situation([
        down({ quiet }),
        down({ open_incident_id: "inc-1" }),
        service({ health: "stopped" }),
      ]),
    ).toEqual({ kind: "quiet" });
  });
});

describe("watchReason", () => {
  const container = service().containers[0];
  const failing = (url: string, code: number | null) =>
    ({ url, status: "failing", last_checked: null, status_code: code, latency_ms: null }) as const;
  const reason = (overrides: Partial<ServiceView>) =>
    watchReason(service({ health: "down", ...overrides }), MESSAGES.en);

  it("names a dead container before a failing URL", () => {
    const dead = { ...container, state: "exited", exit_code: 137 };
    expect(reason({ containers: [dead], urls: [failing("http://a/", 502)] })).toEqual({
      reason: "a container is down",
      detail: "shop-web-1 · exited (137)",
    });
  });

  it("does not count a clean stop as down", () => {
    const stopped = { ...container, name: "shop-worker-1", state: "exited", exit_code: 0 };
    expect(
      reason({ containers: [container, stopped], urls: [failing("http://a/", null)] }),
    ).toEqual({ reason: "its URL check fails", detail: "http://a/" });
  });

  it("counts the failing URL checks without claiming how often they failed", () => {
    const ok = service().urls[0];
    expect(reason({ urls: [failing("http://a/", 500), ok] }).reason).toBe(
      "one of its URL checks fails",
    );
    expect(reason({ urls: [failing("http://a/", 500), failing("http://b/", 500), ok] })).toEqual({
      reason: "2 of its 3 URL checks fail",
      detail: "http://a/ · 500",
    });
  });

  it("says only that it is down when nothing more is known", () => {
    expect(reason({ urls: [] })).toEqual({ reason: "it is down" });
  });
});

describe("watchedNames", () => {
  const named = (...names: string[]) =>
    watchedNames(
      names.map((name) => service({ id: name, name })),
      MESSAGES.en,
    );

  it("names up to three, then two and how many more", () => {
    expect(named("a", "b", "c")).toBe("a, b and c");
    expect(named("a", "b", "c", "d", "e")).toBe("a, b and 3 more");
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
    expect(reach([asleep], [])).toBe("It is fine.");
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
    expect(reach([host({ health: "down" })], services)).toBe("It is fine.");
  });

  it("still counts a service that is not quiet next to a quiet one", () => {
    const services = [
      service({ health: "down", quiet: leftAlone }),
      service({ id: "demo/compose:blog", name: "blog", health: "down" }),
    ];
    const line = reach([host({ health: "down" })], services);
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
    expect(reach(three({ health: "down" }), services)).toBe(
      "Only Demo stack is hurting: 1 of its 3 services. The other two are fine.",
    );
  });

  it("says when a whole host is out of reach", () => {
    expect(reach(three({ reachable: false, health: "down" }), [])).toBe(
      "Demo stack is out of reach as a whole. The other two are fine.",
    );
  });

  it("names every host that is hurting", () => {
    const hosts = [
      host({ id: "a", name: "A", health: "degraded" }),
      host({ id: "b", name: "B", reachable: false, health: "unknown" }),
      host({ id: "c", name: "C" }),
    ];
    expect(reach(hosts, [])).toBe("A and B are hurting. The other one is fine.");
  });

  it("covers all of a host's services, and its only one", () => {
    const one = [host({ health: "down" })];
    expect(reach(one, [service({ health: "down" })])).toBe(
      "Demo stack is hurting: its only service.",
    );
    expect(
      reach(one, [
        service({ id: "demo/a", health: "down" }),
        service({ id: "demo/b", health: "degraded" }),
      ]),
    ).toBe("Demo stack is hurting: all 2 of its services.");
  });

  it("says all is fine in as few words as fit", () => {
    expect(reach([host()], [])).toBe("It is fine.");
    expect(reach([host({ id: "a" }), host({ id: "b" })], [])).toBe("Both are fine.");
    expect(reach(three(), [])).toBe("All three are fine.");
    expect(reach(three({ health: "unknown" }), [])).toBe("All three are fine.");
  });

  it("reads in German with the German names of the counts", () => {
    const services = [
      service({ id: "demo/a", host_id: "demo", health: "down" }),
      service({ id: "demo/b", host_id: "demo", health: "ok" }),
      service({ id: "demo/c", host_id: "demo", health: "stopped" }),
    ];
    const de = (hosts: HostView[], rest: ServiceView[]) => reachLine(hosts, rest, MESSAGES.de);
    expect(de(three({ health: "down" }), services)).toBe(
      "Nur Demo stack hat Probleme: 1 seiner 3 Dienste. Die anderen zwei sind in Ordnung.",
    );
    expect(de(three({ reachable: false, health: "down" }), [])).toBe(
      "Demo stack ist als Ganzes nicht erreichbar. Die anderen zwei sind in Ordnung.",
    );
    expect(de(three(), [])).toBe("Alle drei sind in Ordnung.");
    expect(de([host({ health: "down" })], [service({ health: "down" })])).toBe(
      "Demo stack hat Probleme: sein einziger Dienst.",
    );
  });
});

describe("the plan line's remaining steps", () => {
  it("name the count and keep a verb in German", () => {
    const { overview, counts } = MESSAGES.de;
    expect(overview.planMore(counts.moreSteps(1))).toBe(", und noch 1 weiteren Schritt ausführen");
    expect(overview.planMore(counts.moreSteps(3))).toBe(", und noch 3 weitere Schritte ausführen");
    expect(MESSAGES.en.overview.planMore(MESSAGES.en.counts.moreSteps(3))).toBe(
      ", and 3 more steps",
    );
  });
});
