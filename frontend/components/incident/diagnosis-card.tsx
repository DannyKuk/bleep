import { ChevronRight, CircleHelp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Diagnosis } from "@/lib/api-types";
import { useT } from "@/lib/i18n";

/** The first `count` sentences of `text`, and the rest (empty when nothing was cut). */
export function firstSentences(text: string, count: number): [string, string] {
  // A sentence ends at a stop followed by a space, so "1.2.3" or "db:5432." stay whole.
  const sentences = text.trim().split(/(?<=[.!?])\s+/);
  return [sentences.slice(0, count).join(" "), sentences.slice(count).join(" ")];
}

/**
 * What Bleep thinks is wrong, how sure it is, and what it saw. `short` is the phone's version:
 * the cause in two sentences, with the rest of it and the evidence folded under "More".
 */
export function DiagnosisCard({
  diagnosis,
  short = false,
}: {
  diagnosis: Diagnosis;
  short?: boolean;
}) {
  const t = useT();
  if (short) return <ShortDiagnosis diagnosis={diagnosis} />;
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className="px-3 py-1 text-[13px] font-extrabold text-ink">
          {t.diagnosis.sure(Math.round(diagnosis.confidence * 100))}
        </Badge>
        <Badge className="px-3 py-1 text-[13px]">
          {t.category[diagnosis.category] ?? diagnosis.category}
        </Badge>
      </div>
      {diagnosis.unsure && (
        <p
          role="note"
          className="flex items-start gap-2 rounded-row bg-warn-bg px-4 py-3 text-[15px] text-warn"
        >
          <CircleHelp aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="font-extrabold">{t.diagnosis.notSure}</strong>{" "}
            {t.diagnosis.notSureWhy}
          </span>
        </p>
      )}
      <p className="text-[17px] leading-relaxed">{diagnosis.root_cause}</p>
      {diagnosis.evidence.length > 0 && (
        <ul aria-label={t.diagnosis.evidence} className="flex flex-col gap-2 text-[15px]">
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
          {t.diagnosis.affects}{" "}
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

function ShortDiagnosis({ diagnosis }: { diagnosis: Diagnosis }) {
  const t = useT();
  const [lead, rest] = firstSentences(diagnosis.root_cause, 2);
  const affected = diagnosis.affected_services;
  const more = rest !== "" || diagnosis.evidence.length > 0 || affected.length > 0;
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-sm font-extrabold text-ink-2">
        {t.diagnosis.sure(Math.round(diagnosis.confidence * 100))} ·{" "}
        {t.category[diagnosis.category] ?? diagnosis.category}
      </p>
      {diagnosis.unsure && (
        <p
          role="note"
          className="flex items-start gap-2 rounded-row bg-warn-bg px-3.5 py-2.5 text-[15px] text-warn"
        >
          <CircleHelp aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="font-extrabold">{t.diagnosis.notSure}</strong>{" "}
            {t.diagnosis.notSureShort}
          </span>
        </p>
      )}
      <p className="text-base leading-snug">{lead}</p>
      {more && (
        <details className="group">
          <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-1 text-sm font-bold text-ink-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              className="size-4 transition-transform group-open:rotate-90 motion-reduce:transition-none"
            />
            {t.diagnosis.more}
          </summary>
          <div className="flex flex-col gap-2 pt-1 text-[15px]">
            {rest && <p>{rest}</p>}
            {diagnosis.evidence.length > 0 && (
              <ul aria-label={t.diagnosis.evidence} className="flex flex-col gap-1.5">
                {diagnosis.evidence.map((item, index) => (
                  <li key={index} className="flex flex-wrap items-baseline gap-x-2">
                    <span>{item.detail}</span>
                    <code className="font-mono text-xs text-ink-2">{item.source}</code>
                  </li>
                ))}
              </ul>
            )}
            {affected.length > 0 && (
              <p className="text-sm text-ink-2">
                {t.diagnosis.affects} <code className="font-mono">{affected.join(", ")}</code>
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  );
}
