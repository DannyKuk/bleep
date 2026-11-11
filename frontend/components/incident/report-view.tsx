import { Check, Minus, TriangleAlert, X } from "lucide-react";

import { Bleep } from "@/components/bleep/bleep";
import { LOOK } from "@/components/incident/colour-code";
import { subjectText } from "@/components/incident/incident-list";
import { ShortNamePrefix } from "@/components/incident/short-name";
import { useTimeZone } from "@/components/preferences";
import type { Incident, Outcome, Report, ReportStep } from "@/lib/api-types";
import { formatDateTime, formatTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { reportMood } from "@/lib/mood";
import { cn } from "@/lib/utils";

// Only a verified fix, or a look whose checks found the service healthy, is shown as good news; an
// action nobody confirmed is a warning.
const OUTCOME_TONE: Partial<Record<Outcome, string>> = {
  resolved: "bg-ok-bg text-ok",
  healthy: "bg-ok-bg text-ok",
  unverified: "bg-warn-bg text-warn",
  budget: "bg-warn-bg text-warn",
};
const NEUTRAL_TONE = "bg-stop-bg text-stop";

// A printed page is white whatever the theme: pin the tokens the card uses to their light values.
const PRINT_TOKENS =
  "print:[--surface:#ffffff] print:[--soft:#eeedf8] print:[--line:#cfcde0] print:[--ink:#1c1b33] print:[--ink-2:#46445c] print:[--ok:#1c7448] print:[--ok-bg:#e2f4ea] print:[--warn:#835400] print:[--warn-bg:#fbefd6] print:[--stop:#566075] print:[--stop-bg:#eceef3] print:border-line print:shadow-none";

function OutcomeIcon({ outcome }: { outcome: Outcome | null }) {
  if (outcome === "resolved" || outcome === "healthy") {
    return <Check aria-hidden className="size-4" />;
  }
  if (outcome === "unverified" || outcome === "budget") {
    return <TriangleAlert aria-hidden className="size-4" />;
  }
  return <Minus aria-hidden className="size-4" />;
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="flex break-inside-avoid flex-col gap-1.5">
      <h3 className="text-[13px] font-extrabold tracking-[0.06em] text-ink-2 uppercase">{label}</h3>
      {children}
    </section>
  );
}

/** The mark before a step, in the incident page's colour code: a check only when it is done, a
 * cross when it failed or was refused, a dash for one that never ran. The step's words say the
 * same. */
function StepMark({ status }: { status: ReportStep["status"] | null }) {
  if (status === "done") {
    return <Check aria-hidden className={cn("mt-1 size-4 shrink-0", LOOK.done.ink)} />;
  }
  if (status === "failed" || status === "refused") {
    return <X aria-hidden className={cn("mt-1 size-4 shrink-0", LOOK.failed.ink)} />;
  }
  return <Minus aria-hidden className={cn("mt-1 size-4 shrink-0", LOOK.todo.ink)} />;
}

/** The status of a step line stored before the backend sent `steps`. Such a line is English by
 * definition (the backend translated nothing then), so its ending can be read. */
function legacyStatus(text: string): ReportStep["status"] | null {
  if (/: done$/.test(text)) return "done";
  if (/: failed$/.test(text)) return "failed";
  if (/: refused$/.test(text)) return "refused";
  return null;
}

/** One row per step, from the report's `steps`: the backend writes each in the picked language
 * and gives its status as a field. A report stored before that has only `action_taken`, which
 * the backend joined with "; ". */
function Steps({ report }: { report: Report }) {
  const t = useT().report;
  const rows: { text: string; status: ReportStep["status"] | null }[] =
    report.steps.length > 0
      ? report.steps
      : (report.action_taken?.split("; ") ?? []).map((text) => ({
          text,
          status: legacyStatus(text),
        }));
  if (rows.length === 0) {
    return (
      <p className="flex items-start gap-2">
        <Minus aria-hidden className="mt-1 size-4 shrink-0 text-ink-2" />
        <span className="min-w-0">{t.nothingRan}</span>
      </p>
    );
  }
  return (
    <ul aria-label={t.steps} className="flex flex-col gap-1">
      {rows.map((step, index) => (
        <li key={index} className="flex items-start gap-2">
          <StepMark status={step.status} />
          <span className="min-w-0">{step.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The incident's written report as a short document: how it ended, why, what ran, who decided and
 * what is next. The text is the backend's; this adds only the labels. `incident` adds the subject,
 * times and cost under the title.
 */
export function ReportView({
  report,
  outcome,
  incident,
}: {
  report: Report;
  outcome: Outcome | null;
  incident?: Incident;
}) {
  const noAction = outcome === "no_action";
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const words = t.report;
  return (
    <article
      aria-label={words.label}
      className={cn(
        "flex flex-col gap-5 rounded-card border border-line bg-surface p-5 text-[15px] text-ink sm:p-7",
        PRINT_TOKENS,
      )}
    >
      <header className="flex items-start gap-4">
        <Bleep
          mood={outcome ? reportMood(outcome) : "resting"}
          size={72}
          className="shrink-0 print:hidden"
        />
        <div className="flex min-w-0 flex-col gap-2">
          <p
            className={cn(
              "inline-flex w-fit items-center gap-1.5 rounded-full px-3 py-1 text-sm font-extrabold",
              (outcome && OUTCOME_TONE[outcome]) ?? NEUTRAL_TONE,
            )}
          >
            <OutcomeIcon outcome={outcome} />
            {report.outcome}
          </p>
          <h2 className="text-xl font-extrabold">
            <ShortNamePrefix name={incident?.short_name ?? null} />
            {report.title}
          </h2>
          {incident && (
            <p className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-ink-2">
              <span>{subjectText(incident, t)}</span>
              <span>
                <time dateTime={incident.opened_at}>
                  {formatDateTime(incident.opened_at, zone, language)}
                </time>
                {incident.closed_at && (
                  <>
                    {words.to}
                    <time dateTime={incident.closed_at}>
                      {formatTime(incident.closed_at, zone, language)}
                    </time>
                  </>
                )}
              </span>
              {incident.cost_usd != null && <span>${incident.cost_usd.toFixed(3)}</span>}
            </p>
          )}
        </div>
      </header>

      <Section label={words.short}>
        <p>{report.summary}</p>
      </Section>
      <Section label={words.why}>
        <p>{report.cause}</p>
      </Section>
      <Section label={words.did}>
        <Steps report={report} />
      </Section>
      {noAction && report.follow_ups.length > 0 && (
        <Section label={words.youCan}>
          <ul
            aria-label={words.youCan}
            className="flex list-disc flex-col gap-1 rounded-row bg-soft py-3 pr-4 pl-8"
          >
            {report.follow_ups.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}
      <Section label={words.decision}>
        <p>{report.human_decision ?? words.notAsked}</p>
      </Section>
      {report.timeline.length > 0 && (
        <Section label={words.timeline}>
          <ol aria-label={words.timelineLabel} className="flex flex-col gap-1">
            {report.timeline.map(({ at, text }, index) => (
              <li key={index} className="flex items-baseline gap-3">
                <time dateTime={at} className="shrink-0 font-mono text-xs text-ink-2 tabular-nums">
                  {formatTime(at, zone, language)}
                </time>
                <span className="min-w-0">{text}</span>
              </li>
            ))}
          </ol>
        </Section>
      )}
      {!noAction && report.follow_ups.length > 0 && (
        <Section label={words.followUps}>
          <ul aria-label={words.followUps} className="flex list-disc flex-col gap-1 pl-5">
            {report.follow_ups.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      )}
    </article>
  );
}
