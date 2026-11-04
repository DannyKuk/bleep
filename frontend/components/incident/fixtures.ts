import type {
  ApprovalView,
  Diagnosis,
  Incident,
  IncidentDetail,
  IncidentEvent,
  Proposal,
  Report,
} from "@/lib/api-types";

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: "inc-1",
    subject: { kind: "service", host_id: "demo", service_id: "demo/compose:shop" },
    status: "awaiting_approval",
    opened_at: "2026-11-02T18:00:00Z",
    closed_at: null,
    title: "shop-web-1 died",
    cost_usd: 0.012,
    tokens: 5400,
    trace_url: null,
    prompt_overrides: null,
    ...overrides,
  };
}

export function diagnosis(overrides: Partial<Diagnosis> = {}): Diagnosis {
  return {
    root_cause: "The web container was stopped and its restart policy is no",
    category: "config",
    evidence: [{ source: "inspect_container", detail: "RestartPolicy is no, exit code 137" }],
    confidence: 0.82,
    affected_services: ["shop"],
    unsure: false,
    planted_in: [],
    ...overrides,
  };
}

export function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    kind: "act",
    steps: [{ action: "docker.start", host_id: "demo", target: "shop-web-1", params: {} }],
    reason: "Starting it brings the shop back; nothing in the logs says it will fail again",
    verification: { containers: ["shop-web-1"], urls: ["http://localhost:8080/"] },
    human_instructions: null,
    refused: false,
    ...overrides,
  };
}

export function approval(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return {
    id: "appr-1",
    status: "pending",
    plan_hash: "abc",
    proposal: proposal(),
    requested_at: "2026-11-02T18:02:00Z",
    reminders: 0,
    decision: null,
    ...overrides,
  };
}

export function report(overrides: Partial<Report> = {}): Report {
  return {
    title: "shop-web-1 died",
    summary: "shop-web was stopped and nothing restarted it. Bleep started it again.",
    cause: "Someone stopped shop-web-1 and its restart policy is no.",
    action_taken: "docker.start shop-web-1 on demo: done",
    outcome: "Fixed and verified",
    timeline: [
      { at: "2026-11-02T18:00:00Z", text: "Signal: container died (shop-web-1)" },
      { at: "2026-11-02T18:03:00Z", text: "Approved on Telegram" },
      { at: "2026-11-02T18:04:00Z", text: "Check 1 passed" },
    ],
    follow_ups: ["Set restart: unless-stopped on shop-web.", "Find out who stopped it."],
    human_decision: "Approved on Telegram",
    ...overrides,
  };
}

export function detail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    incident: incident(),
    diagnosis: diagnosis(),
    proposal: proposal(),
    approval: approval(),
    outcome: null,
    report: null,
    feedback: [],
    ...overrides,
  };
}

let nextSeq = 1;

export function event(
  kind: string,
  payload: Record<string, unknown> = {},
  seq?: number,
): IncidentEvent {
  return {
    incident_id: "inc-1",
    seq: seq ?? nextSeq++,
    kind,
    payload,
    at: "2026-11-02T18:01:00Z",
  };
}
