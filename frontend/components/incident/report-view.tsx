import type { Outcome, Report } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

// Only a verified fix is shown as good news; an action nobody confirmed is a warning.
const OUTCOME_TONE: Partial<Record<Outcome, string>> = {
  resolved: "text-health-ok",
  unverified: "text-health-degraded",
  budget: "text-health-degraded",
};

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="shrink-0 text-xs font-medium text-muted-foreground sm:w-20 sm:pt-0.5">
        {label}
      </dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** The incident's written report: how it ended, why, what ran, who decided, and what is next. */
export function ReportView({ report, outcome }: { report: Report; outcome: Outcome | null }) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p
        className={cn(
          "font-semibold",
          (outcome && OUTCOME_TONE[outcome]) ?? "text-muted-foreground",
        )}
      >
        {report.outcome}
      </p>
      <p>{report.summary}</p>
      <dl className="flex flex-col gap-1.5">
        <Fact label="Cause">{report.cause}</Fact>
        <Fact label="Action">{report.action_taken ?? "Nothing ran."}</Fact>
        <Fact label="Decision">{report.human_decision ?? "You were not asked."}</Fact>
      </dl>
      {report.follow_ups.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted-foreground">Next</h3>
          <ul aria-label="Follow-ups" className="flex list-disc flex-col gap-1 pl-5">
            {report.follow_ups.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {report.timeline.length > 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted-foreground">Timeline</h3>
          <ol aria-label="Report timeline" className="flex flex-col gap-1">
            {report.timeline.map(({ at, text }, index) => (
              <li key={index} className="flex items-baseline gap-3">
                <time dateTime={at} className="shrink-0 text-xs text-muted-foreground tabular-nums">
                  {formatTime(at)}
                </time>
                <span className="min-w-0">{text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
