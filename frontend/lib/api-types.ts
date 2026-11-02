// Hand-written from the M1 API contract (Tasks 8 and 9). Once Task 8 lands, `npm run gen:api`
// regenerates this file from the backend's /openapi.json; keep the exported names below
// (HostView, ServiceView, ContainerView, UrlStatus, Health) when switching.

export type Health = "ok" | "degraded" | "down" | "unknown";

export type HostKind = "ssh" | "socket";

export interface DiskUsage {
  mount: string;
  used_percent: number;
}

export interface HostBasics {
  os: string;
  cpus: number;
  mem_total: number;
  mem_available: number;
  load1: number;
  load15: number;
  disks: DiskUsage[];
}

export interface HostView {
  id: string;
  name: string;
  kind: HostKind;
  reachable: boolean;
  last_seen: string | null;
  error: string | null;
  basics: HostBasics | null;
  service_count: number;
  health: Health;
}

export type ContainerHealth = "healthy" | "unhealthy" | "starting" | "none";

export interface ContainerView {
  id: string;
  name: string;
  image: string;
  state: string;
  health: ContainerHealth;
  restart_count: number;
  protected: boolean;
}

export interface UrlStatus {
  url: string;
  status: "unknown" | "ok" | "failing";
  last_checked: string | null;
  status_code: number | null;
  latency_ms: number | null;
}

export type ServiceSource = "coolify" | "compose" | "single";

export interface ServiceView {
  id: string;
  host_id: string;
  name: string;
  source: ServiceSource;
  health: Health;
  containers: ContainerView[];
  urls: UrlStatus[];
  open_incident_id: string | null;
}
