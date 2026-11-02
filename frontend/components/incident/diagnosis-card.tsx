import { CircleHelp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Diagnosis } from "@/lib/api-types";

/** What Bleep thinks is wrong, how sure it is, and what it saw. */
export function DiagnosisCard({ diagnosis }: { diagnosis: Diagnosis }) {
  return (
    <div className="flex flex-col gap-3">
      {diagnosis.unsure && (
        <p
          role="note"
          className="flex items-start gap-2 rounded-md bg-health-degraded/12 px-3 py-2 text-sm text-health-degraded"
        >
          <CircleHelp aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="font-semibold">I am not sure.</strong> The evidence does not settle
            the cause; read it before you decide.
          </span>
        </p>
      )}
      <p className="text-sm">{diagnosis.root_cause}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{diagnosis.category}</Badge>
        <span className="text-xs text-muted-foreground">
          {Math.round(diagnosis.confidence * 100)}% confident
        </span>
        {diagnosis.affected_services.length > 0 && (
          <span className="text-xs text-muted-foreground">
            Affects {diagnosis.affected_services.join(", ")}
          </span>
        )}
      </div>
      {diagnosis.evidence.length > 0 && (
        <ul aria-label="Evidence" className="flex flex-col gap-1 text-sm">
          {diagnosis.evidence.map((item, index) => (
            <li key={index} className="flex flex-wrap gap-x-2">
              <code className="font-mono text-xs text-muted-foreground">{item.source}</code>
              <span>{item.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
