"use client";

import { useRef, useState } from "react";

import type { UrlCheckPoint } from "@/lib/api-types";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "Passed at 14:02:30, 200, 42 ms"; a check with no reply has no status code. */
export function checkWords(check: UrlCheckPoint): string {
  const parts = [
    `${check.ok ? "Passed" : "Failed"} at ${formatTime(check.checked_at)}`,
    check.status_code != null ? String(check.status_code) : "no reply",
    `${check.latency_ms} ms`,
  ];
  return parts.join(", ");
}

function summary(checks: UrlCheckPoint[]): string {
  if (checks.length === 1) return `Last check: ${checks[0].ok ? "passed" : "failed"}`;
  const failed = checks.filter((check) => !check.ok).length;
  return `Last ${checks.length} checks: ${failed === 0 ? "all passed" : `${failed} failed`}`;
}

/** Where an arrow key, Home or End moves from tick `from` of `count`; null for any other key. */
function target(key: string, from: number, count: number): number | null {
  const moves: Record<string, number> = {
    ArrowLeft: Math.max(0, from - 1),
    ArrowRight: Math.min(count - 1, from + 1),
    Home: 0,
    End: count - 1,
  };
  return key in moves ? moves[key] : null;
}

/**
 * A URL's recent checks as ticks, oldest left: green passed, red (and taller) failed. One tick is
 * in the tab order and the arrow keys move along the rest; the line under the strip says the
 * hovered or focused tick's time, status code and latency, else how many failed.
 */
export function CheckStrip({ url, checks }: { url: string; checks: UrlCheckPoint[] }) {
  const [active, setActive] = useState<number | null>(null);
  const [tabStop, setTabStop] = useState<number | null>(null); // null: the newest
  const ticks = useRef<(HTMLSpanElement | null)[]>([]);
  if (checks.length === 0) return null;
  const stop = Math.min(tabStop ?? checks.length - 1, checks.length - 1);

  const move = (event: React.KeyboardEvent, from: number) => {
    const to = target(event.key, from, checks.length);
    if (to === null) return;
    event.preventDefault();
    setTabStop(to);
    ticks.current[to]?.focus();
  };

  const shown = active !== null ? checks[active] : undefined;
  return (
    <div className="flex flex-col gap-1">
      <ol
        aria-label={`${summary(checks)}, oldest first, for ${url}`}
        className="flex h-6 items-end gap-[3px]"
        onMouseLeave={() => setActive(null)}
      >
        {checks.map((check, index) => (
          <li
            key={`${check.checked_at}-${index}`}
            className="flex h-full max-w-2.5 flex-1 items-end"
          >
            <span
              ref={(node) => {
                ticks.current[index] = node;
              }}
              role="img"
              aria-label={checkWords(check)}
              data-ok={check.ok}
              tabIndex={index === stop ? 0 : -1}
              onMouseEnter={() => setActive(index)}
              onFocus={() => {
                setActive(index);
                setTabStop(index);
              }}
              onBlur={() => setActive(null)}
              onKeyDown={(event) => move(event, index)}
              className={cn(
                "block w-full min-w-[3px] rounded-[2px] outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring",
                check.ok ? "h-4 bg-ok" : "h-6 bg-down",
                index === active && "outline-2 outline-ink",
              )}
            />
          </li>
        ))}
      </ol>
      <p className="text-xs text-ink-2">{shown ? checkWords(shown) : summary(checks)}</p>
    </div>
  );
}
