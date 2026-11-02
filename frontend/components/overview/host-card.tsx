import { WifiOff } from "lucide-react";

import { HealthBadge } from "@/components/overview/health-badge";
import { ServiceRow } from "@/components/overview/service-row";
import { Badge } from "@/components/ui/badge";
import type { HostView, ServiceView } from "@/lib/api-types";

export function HostCard({ host, services }: { host: HostView; services: ServiceView[] }) {
  const titleId = `host-${host.id}`;
  return (
    <section aria-labelledby={titleId} className="rounded-lg border border-border bg-card">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
        <h2 id={titleId} className="text-sm font-semibold">
          {host.name}
        </h2>
        <Badge>{host.kind}</Badge>
        <HealthBadge health={host.health} />
        <span className="text-xs text-muted-foreground">
          {host.service_count} {host.service_count === 1 ? "service" : "services"}
        </span>
        {!host.reachable && (
          <p className="flex basis-full items-start gap-1.5 text-xs font-medium text-health-down">
            <WifiOff aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>Unreachable{host.error ? `: ${host.error}` : ""}</span>
          </p>
        )}
      </header>
      {services.length > 0 && (
        <div className="divide-y divide-border border-t border-border px-4">
          {services.map((service) => (
            <ServiceRow key={service.id} service={service} />
          ))}
        </div>
      )}
    </section>
  );
}
