"use client";

import { Filter, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useId, useState } from "react";

import { Failed, Loading, useGet } from "@/components/dev/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { GuardEntry, GuardKind } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";

export const KINDS: Record<GuardKind, string> = {
  refused_command: "Refused command",
  redacted: "Redacted",
  blocked_action: "Blocked action",
  injection_marker: "Injection marker",
  budget: "Budget",
  foreign_chat: "Foreign chat",
  prompt_override: "Prompt override",
};

export function guardLogPath(kind: GuardKind | "", incident: string): string {
  const query = new URLSearchParams();
  if (kind) query.set("kind", kind);
  if (incident) query.set("incident_id", incident);
  const search = query.toString();
  return `/api/dev/guard-log${search ? `?${search}` : ""}`;
}

export function GuardEntries({
  entries,
  onIncident,
}: {
  entries: GuardEntry[];
  onIncident: (incidentId: string) => void;
}) {
  if (entries.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ShieldCheck aria-hidden className="size-4" />
        Nothing in the guard log for this filter.
      </p>
    );
  }
  return (
    <ul aria-label="Guard log" className="flex flex-col divide-y divide-border">
      {entries.map((entry) => (
        <li key={entry.id} className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge>{KINDS[entry.kind] ?? entry.kind}</Badge>
            <button
              type="button"
              title="Show only this incident's entries"
              onClick={() => onIncident(entry.incident_id)}
              className="rounded-sm font-mono underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {entry.incident_id}
            </button>
            <time dateTime={entry.at}>{formatDateTime(entry.at)}</time>
          </div>
          <span className="font-mono text-xs break-words">{entry.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** What the guard refused, blocked or changed, newest first, filtered by kind and incident. */
export function GuardLogPanel() {
  const [kind, setKind] = useState<GuardKind | "">("");
  const [typed, setTyped] = useState("");
  const [incident, setIncident] = useState("");
  const [reload, setReload] = useState(0);
  const { data, error } = useGet<GuardEntry[]>(guardLogPath(kind, incident), reload);
  const id = useId();

  function showIncident(incidentId: string) {
    setTyped(incidentId);
    setIncident(incidentId);
  }

  return (
    <div className="flex flex-col gap-3">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(submitted) => {
          submitted.preventDefault();
          setIncident(typed.trim());
        }}
      >
        <div className="flex min-w-40 flex-col gap-1.5">
          <label htmlFor={`${id}-kind`} className="text-xs text-muted-foreground">
            Kind
          </label>
          <select
            id={`${id}-kind`}
            value={kind}
            onChange={(changed) => setKind(changed.target.value as GuardKind | "")}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <option value="">Every kind</option>
            {(Object.keys(KINDS) as GuardKind[]).map((key) => (
              <option key={key} value={key}>
                {KINDS[key]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <label htmlFor={`${id}-incident`} className="text-xs text-muted-foreground">
            Incident
          </label>
          <Input
            id={`${id}-incident`}
            value={typed}
            maxLength={200}
            placeholder="Every incident"
            className="font-mono"
            onChange={(changed) => setTyped(changed.target.value)}
          />
        </div>
        <Button type="submit" variant="outline">
          <Filter aria-hidden />
          Filter
        </Button>
        {incident && (
          <Button type="button" variant="ghost" onClick={() => showIncident("")}>
            <X aria-hidden />
            Every incident
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Refresh the guard log"
          title="Refresh"
          onClick={() => setReload((n) => n + 1)}
        >
          <RefreshCw aria-hidden />
        </Button>
      </form>
      {error && <Failed>Could not load the guard log: {error.message}</Failed>}
      {!data && !error && <Loading>Loading the guard log</Loading>}
      {data && <GuardEntries entries={data} onIncident={showIncident} />}
    </div>
  );
}
