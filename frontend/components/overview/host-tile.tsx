import { ArrowUpRight, Moon } from "lucide-react";
import Link from "next/link";

import { HealthBadge } from "@/components/overview/health-badge";
import { isHurting } from "@/components/overview/situation";
import { QuietMenu } from "@/components/quiet/quiet-menu";
import { QuietPill } from "@/components/quiet/quiet-pill";
import { Button } from "@/components/ui/button";
import type { HostView, ServiceView } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { serviceHref } from "@/lib/routes";
import { cn } from "@/lib/utils";

const FOCUS = "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/** "load 0.3 · mem 75% · disk 58%" (the fullest disk), or when an unreachable host was last seen. */
export function basicsLine(host: HostView): string | null {
  if (!host.reachable) {
    return host.last_seen ? `last seen ${formatDateTime(host.last_seen)}` : "never reached";
  }
  const basics = host.basics;
  if (!basics) return null;
  const parts: string[] = [];
  if (basics.load1 != null) parts.push(`load ${basics.load1.toFixed(1)}`);
  if (basics.mem_total && basics.mem_available != null) {
    const used = 1 - basics.mem_available / basics.mem_total;
    parts.push(`mem ${Math.round(used * 100)}%`);
  }
  if (basics.disks.length > 0) {
    parts.push(`disk ${Math.max(...basics.disks.map((disk) => disk.used_percent))}%`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

// Fine pointers see the moon on hover or focus (it stays reachable by keyboard); touch screens
// have no hover, so it is always there.
const MOON_REVEAL =
  "pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:focus-visible:opacity-100 pointer-fine:data-popup-open:opacity-100";

function ServicePill({ service, onChanged }: { service: ServiceView; onChanged: () => void }) {
  return (
    <li className="group flex min-h-11 items-center gap-2 rounded-field bg-surface pr-1.5 pl-1">
      <Link
        href={serviceHref(service.id)}
        className={cn(
          "flex min-h-11 min-w-0 flex-1 items-center truncate rounded-field px-2.5 text-[15px] font-bold underline-offset-2 hover:underline",
          FOCUS,
        )}
      >
        {service.name}
      </Link>
      {service.open_incident_id && (
        <Link
          href={`/incidents/${service.open_incident_id}`}
          aria-label={`Open incident on ${service.name}`}
          className={cn(
            "inline-flex min-h-11 shrink-0 items-center gap-0.5 rounded-full px-1.5 text-[13px] font-bold text-down underline-offset-2 hover:underline",
            FOCUS,
          )}
        >
          Incident
          <ArrowUpRight aria-hidden className="size-3.5" />
        </Link>
      )}
      <QuietMenu
        target={{ scope: "service", id: service.id, name: service.name }}
        quiet={service.quiet}
        onChanged={onChanged}
        trigger={
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Quiet ${service.name}`}
            className={cn("shrink-0", MOON_REVEAL)}
          >
            <Moon aria-hidden />
          </Button>
        }
      />
      {service.quiet ? (
        <QuietPill quiet={service.quiet} />
      ) : (
        <HealthBadge health={service.health} className="shrink-0" />
      )}
    </li>
  );
}

/** One host: its name and kind, its health, its services as rows, and its basics in mono. A host
 * with a problem is tinted, so the eye lands on it first. */
export function HostTile({
  host,
  services,
  onChanged = () => undefined,
}: {
  host: HostView;
  services: ServiceView[];
  /** Called after a quiet state was set or cleared on the host or one of its services. */
  onChanged?: () => void;
}) {
  const titleId = `host-${host.id}`;
  const hurting = isHurting(host, services);
  const basics = basicsLine(host);
  return (
    <section
      aria-labelledby={titleId}
      data-hurting={hurting}
      className={cn(
        "flex min-w-0 flex-col gap-3.5 rounded-card-sm p-4 md:p-5",
        hurting
          ? "border-[1.5px] border-down bg-down-bg"
          : "border-[1.5px] border-transparent bg-soft",
      )}
    >
      <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <h3 id={titleId} className="text-[17px] font-extrabold">
          {host.name}
        </h3>
        <span className="text-xs font-bold text-ink-2">{host.kind}</span>
        <span className="ml-auto flex items-center gap-1">
          {host.quiet ? (
            <QuietPill quiet={host.quiet} />
          ) : (
            <HealthBadge health={host.health} label={host.reachable ? undefined : "Unreachable"} />
          )}
          <QuietMenu
            target={{ scope: "host", id: host.id, name: host.name }}
            quiet={host.quiet}
            onChanged={onChanged}
          />
        </span>
      </header>
      {services.length > 0 ? (
        <ul aria-label={`Services on ${host.name}`} className="flex flex-col gap-1">
          {services.map((service) => (
            <ServicePill key={service.id} service={service} onChanged={onChanged} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-2">No services seen.</p>
      )}
      {basics && <p className="font-mono text-xs text-ink-2">{basics}</p>}
      {!host.reachable && host.error && (
        <p className="font-mono text-xs break-words text-down">{host.error}</p>
      )}
    </section>
  );
}
