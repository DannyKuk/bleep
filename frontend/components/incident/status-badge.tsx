import { HealthBadge } from "@/components/overview/health-badge";
import type { Health, IncidentStatus } from "@/lib/api-types";
import { useT } from "@/lib/i18n";

// An incident's status in the dashboard's health colours: red when it needs a human or failed,
// amber while Bleep works on it or waits to try the model again, green when fixed, grey when closed.
const HEALTH: Record<IncidentStatus, Health> = {
  open: "degraded",
  investigating: "degraded",
  awaiting_approval: "down",
  executing: "degraded",
  verifying: "degraded",
  paused: "degraded",
  waiting: "degraded",
  resolved: "ok",
  closed: "stopped",
  failed: "down",
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  return <HealthBadge health={HEALTH[status]} label={useT().status[status] ?? status} />;
}
