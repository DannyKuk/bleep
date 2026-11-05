// Hand-written from the API's schemas (backend/src/bleep/schemas/api.py). `npm run gen:api` would
// regenerate the whole file from the backend's /openapi.json; keep the exported names below
// (HostView, ServiceView, ContainerView, UrlStatus, Health) if it is ever switched.

/** `asleep`: a host that may sleep and does not answer, and every service on it. */
export type Health = "ok" | "degraded" | "down" | "stopped" | "unknown" | "asleep";
/** A host's health; the same values as a service's. */
export type HostHealth = Health;

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

/** `schemas/api.py` `QuietView`. On a service, `scope: "host"` means quiet through its host. */
export interface QuietView {
  scope: "service" | "host";
  mode: "snooze" | "leave_alone";
  until: string | null;
  since: string;
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
  health: HostHealth;
  /** A laptop that sleeps by design: not answering opens no incident. */
  may_sleep: boolean;
  quiet: QuietView | null;
  quiet_service_ids: string[];
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
  quiet: QuietView | null;
}

// Incidents (Task 21's contract: schemas/incidents.py, schemas/graph.py, schemas/api.py).

export type IncidentStatus =
  | "open"
  | "investigating"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "paused"
  | "waiting"
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
  /** The link to the incident's trace, when tracing stored one. */
  trace_url: string | null;
  /** The prompt overrides in force when its run started: name to a hash of the text. `null` before
   * the run started, or for a run started before the record existed. */
  prompt_overrides: Record<string, string> | null;
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
  /** Where host text gave orders, from a closed list; it never carries that text (#105). */
  planted_in: PlantedSource[];
}

export type PlantedSource = "logs" | "events" | "inspect" | "http_body" | "memory" | "other";

export type Action =
  | "docker.restart"
  | "docker.stop"
  | "docker.start"
  | "coolify.redeploy"
  | "coolify.set_env"
  | "mcp.call";

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
  /** Bleep's own checks refused the plan twice: it can be instructed or let go, never approved. */
  refused?: boolean;
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

/** The human's vote on an incident's diagnosis and plan, null where there is none. */
export interface IncidentVotes {
  diagnosis: Vote | null;
  proposal: Vote | null;
}

/** An item of `GET /api/incidents`: the incident with the human's votes (not their notes). */
export interface IncidentListItem extends Incident {
  votes: IncidentVotes;
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
/** One past check of a URL (`schemas/api.py` `UrlCheckPoint`): no error text, no body. */
export interface UrlCheckPoint {
  ok: boolean;
  status_code: number | null;
  latency_ms: number;
  checked_at: string;
}

/** A configured URL's recent checks, oldest first (the last 30 at most). */
export interface UrlHistory {
  url: string;
  checks: UrlCheckPoint[];
}

export interface ServiceDetail {
  service: ServiceView;
  rules: string[];
  host_rules: string[];
  /** When the service's current health began; null when the watcher has not judged it yet. */
  health_since: string | null;
  recent_checks: UrlHistory[];
}

// Dev panel (#42: schemas/dev.py, and the model choice and budgets of schemas/settings.py, which
// the panel edits through `PUT /api/settings`). Interfaces, so a second copy of one merges.

export interface ModelChoice {
  provider: "openrouter" | "local";
  model: string;
  cheap_model: string;
  local_model: string;
}

export interface Budgets {
  investigate_tool_calls: number;
  low_confidence: number;
  verify_loops: number;
  replans: number;
  incident_tokens: number;
  daily_cost_usd: number;
  approval_reminder_min: number;
  model_timeout_s: number;
  paused_retry_min: number;
  stuck_after_min: number;
}

/** The part of `GET /api/settings` the dev panel reads: values in force and the overrides. */
export interface DevSettingsView {
  models: ModelChoice;
  budgets: Budgets;
  overrides: { models?: Partial<ModelChoice>; budgets?: Partial<Budgets> };
  /** Sent back with a save, which is refused (409) when the settings changed since. */
  version: string;
}

export interface PromptView {
  /** `area/name`, the file `prompts/<name>.md`. */
  name: string;
  /** The file's placeholders, which an override must use exactly. */
  placeholders: string[];
  file_text: string;
  override: string | null;
  overridden_at: string | null;
  /** Sent back with a save or reset, which is refused (409) when the prompt changed since. */
  version: string;
}

export interface PromptOverrideBody {
  text: string;
}

/** Sums of the usage ledger. `usd` covers the priced calls (null when none is); `unpriced` counts
 * the calls without a known cost. */
export interface Usage {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  usd: number | null;
  unpriced: number;
}

export interface IncidentCost extends Usage {
  incident_id: string;
  title: string | null;
  status: IncidentStatus | null;
  opened_at: string | null;
  trace_url: string | null;
  /** The prompt overrides in force when the incident's run started: name to a hash of the text.
   * `null` when none was recorded (no incident, or a run that started before the record). */
  prompt_overrides: Record<string, string> | null;
}

export interface DayCost extends Usage {
  /** The backend's local day, as the daily cap counts it. */
  day: string;
}

export interface CostView {
  incidents: IncidentCost[];
  days: DayCost[];
  daily_cap_usd: number;
  /** The priced calls only, as the daily cap counts them. */
  spent_today_usd: number;
  /** Today's calls without a known price, left out of `spent_today_usd`. */
  unpriced_today: number;
}

/** What the guard refused, blocked or changed (`guard/log.py` `GuardKind`). */
export type GuardKind =
  | "refused_command"
  | "redacted"
  | "blocked_action"
  | "injection_marker"
  | "budget"
  | "foreign_chat"
  | "prompt_override";

export interface GuardEntry {
  id: number;
  incident_id: string;
  kind: GuardKind;
  detail: string;
  at: string;
}
