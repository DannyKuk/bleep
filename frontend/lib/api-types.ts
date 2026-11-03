// Hand-written from the API contract (Tasks 8, 9 and 21). Once Task 8 lands, `npm run gen:api`
// regenerates this file from the backend's /openapi.json; keep the exported names below
// (HostView, ServiceView, ContainerView, UrlStatus, Health) when switching.

export type Health = "ok" | "degraded" | "down" | "stopped" | "unknown";

export type HostKind = "ssh" | "socket";

export interface DiskView {
  mount: string;
  used_percent: number;
}

export interface BasicsView {
  os: string;
  cpus: number | null;
  mem_total: number | null;
  mem_available: number | null;
  load1: number | null;
  load15: number | null;
  disks: DiskView[];
}

export interface HostView {
  id: string;
  name: string;
  kind: HostKind;
  reachable: boolean;
  last_seen: string | null;
  error: string | null;
  basics: BasicsView | null;
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
  exit_code: number | null;
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

// Incidents (Task 21's contract: schemas/incidents.py, schemas/graph.py, schemas/api.py).

export type IncidentStatus =
  | "open"
  | "investigating"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "resolved"
  | "closed"
  | "failed";

export interface Subject {
  kind: "service" | "host";
  host_id: string;
  service_id: string | null;
}

export interface Incident {
  id: string;
  subject: Subject;
  status: IncidentStatus;
  opened_at: string;
  closed_at: string | null;
  title: string;
  cost_usd: number | null;
  tokens: number;
}

/** One entry of an incident's timeline. `kind` is free-form on the wire; `payload` depends on it. */
export interface IncidentEvent {
  incident_id: string;
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  at: string;
}

export interface RecoveryEvent {
  id: number | null;
  host_id: string;
  service_id: string | null;
  container: string;
  at: string;
  explanation: string;
}

export interface Evidence {
  source: string;
  detail: string;
}

export interface Diagnosis {
  root_cause: string;
  category: "config" | "resource" | "dependency" | "code" | "network" | "host" | "unknown";
  evidence: Evidence[];
  confidence: number;
  affected_services: string[];
  unsure: boolean;
}

export type Action =
  | "docker.restart"
  | "docker.stop"
  | "docker.start"
  | "coolify.redeploy"
  | "coolify.set_env";

export interface PlanStep {
  action: Action;
  host_id: string;
  target: string;
  params: Record<string, string>;
}

export interface VerificationPlan {
  containers: string[];
  urls: string[];
}

export interface Proposal {
  kind: "act" | "no_action";
  steps: PlanStep[];
  reason: string;
  verification: VerificationPlan;
  human_instructions: string | null;
}

export type DecisionKind = "approve" | "reject" | "instruct";
export type Channel = "telegram" | "dashboard";

export interface Decision {
  kind: DecisionKind;
  text: string | null;
  via: Channel;
  at: string;
}

export interface ApprovalView {
  id: string;
  status: "pending" | "approved" | "rejected" | "instructed";
  plan_hash: string;
  proposal: Proposal;
  requested_at: string;
  reminders: number;
  decision: Decision | null;
}

export type Outcome =
  | "resolved"
  | "rejected"
  | "no_action"
  | "unverified"
  | "budget"
  | "unsure"
  | "replan_limit";

export interface TimelineEntry {
  at: string;
  text: string;
}

/** The incident write-up; `outcome` is the fixed text for the run's outcome. */
export interface Report {
  title: string;
  summary: string;
  cause: string;
  action_taken: string | null;
  outcome: string;
  timeline: TimelineEntry[];
  follow_ups: string[];
  human_decision: string | null;
}

export interface IncidentDetail {
  incident: Incident;
  diagnosis: Diagnosis | null;
  proposal: Proposal | null;
  approval: ApprovalView | null;
  outcome: Outcome | null;
  report: Report | null;
  /** The human's votes on the diagnosis and the plan (Task 30), at most one per target. */
  feedback: Feedback[];
}

export interface DecisionBody {
  approval_id: string;
  kind: DecisionKind;
  text?: string;
}

/** The 409 body of a decision that did not count; `decided_via` names the channel that won. */
export interface DecisionConflict {
  detail: string;
  decided_via: Channel | null;
}

// Feedback and memory (Task 30: schemas/graph.py, schemas/api.py).

export type FeedbackTarget = "diagnosis" | "proposal";
export type Vote = "up" | "down";

export interface Feedback {
  target: FeedbackTarget;
  vote: Vote;
  note: string | null;
  at: string;
}

export interface FeedbackBody {
  target: FeedbackTarget;
  vote: Vote;
  note?: string;
}

export interface ServiceFact {
  id: number;
  text: string;
  /** `human` for a fact a person added, `report` for one learned from an incident's report. */
  source: "report" | "human";
  created_at: string;
  /** The incident a report fact came from; null for a person's. */
  incident_id: string | null;
}

export interface FactBody {
  text: string;
}

/** One service for its page: the overview's view plus its rules and its host's. */
export interface ServiceDetail {
  service: ServiceView;
  rules: string[];
  host_rules: string[];
}
