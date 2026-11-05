import { Moon } from "lucide-react";

import type { QuietView } from "@/lib/api-types";
import { quietLabel } from "@/lib/quiet-api";

/** The grey "this one is quiet" pill: what a snooze or a leave-alone says, with a moon. */
export function QuietPill({ quiet }: { quiet: QuietView }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-stop-bg px-2.5 py-0.5 text-xs font-bold text-stop">
      <Moon aria-hidden className="size-3.5" />
      {quietLabel(quiet, new Date())}
    </span>
  );
}
