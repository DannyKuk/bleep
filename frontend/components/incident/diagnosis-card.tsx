import { CircleHelp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Diagnosis } from "@/lib/api-types";

/** What Bleep thinks is wrong, how sure it is, and what it saw. */
export function DiagnosisCard({ diagnosis }: { diagnosis: Diagnosis }) {
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className="px-3 py-1 text-[13px] font-extrabold text-ink">
          {Math.round(diagnosis.confidence * 100)}% sure
        </Badge>
        <Badge className="px-3 py-1 text-[13px]">{diagnosis.category}</Badge>
      </div>
      {diagnosis.unsure && (
        <p
          role="note"
          className="flex items-start gap-2 rounded-row bg-warn-bg px-4 py-3 text-[15px] text-warn"
        >
          <CircleHelp aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="font-extrabold">I am not sure.</strong> The evidence does not settle
            the cause; read it before you decide.
          </span>
        </p>
      )}
      <p className="text-[17px] leading-relaxed">{diagnosis.root_cause}</p>
      {diagnosis.evidence.length > 0 && (
        <ul aria-label="Evidence" className="flex flex-col gap-2 text-[15px]">
          {diagnosis.evidence.map((item, index) => (
            <li key={index} className="flex gap-2.5">
              <span aria-hidden className="font-extrabold text-accent">
                ·
              </span>
              <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                <span>{item.detail}</span>
                <code className="font-mono text-xs text-ink-2">{item.source}</code>
              </span>
            </li>
          ))}
        </ul>
      )}
      {diagnosis.affected_services.length > 0 && (
        <p className="text-sm text-ink-2">
          Affects{" "}
          {diagnosis.affected_services.map((service, index) => (
            <span key={service}>
              {index > 0 && ", "}
              <code className="font-mono">{service}</code>
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
