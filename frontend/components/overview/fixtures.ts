import type { HostView, ServiceView } from "@/lib/api-types";

export function host(overrides: Partial<HostView> = {}): HostView {
  return {
    id: "demo",
    name: "Demo stack",
    kind: "socket",
    reachable: true,
    last_seen: "2026-11-02T18:00:00Z",
    error: null,
    basics: null,
    service_count: 1,
    health: "ok",
    ...overrides,
  };
}

export function service(overrides: Partial<ServiceView> = {}): ServiceView {
  return {
    id: "demo/compose:shop",
    host_id: "demo",
    name: "shop",
    source: "compose",
    health: "ok",
    containers: [
      {
        id: "abc123def456",
        name: "shop-web-1",
        image: "shop:1",
        state: "running",
        health: "healthy",
        restart_count: 0,
        protected: false,
      },
    ],
    urls: [
      {
        url: "http://localhost:8080/",
        status: "ok",
        last_checked: null,
        status_code: 200,
        latency_ms: 12,
      },
    ],
    open_incident_id: null,
    ...overrides,
  };
}
