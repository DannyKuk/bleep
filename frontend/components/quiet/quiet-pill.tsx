import { Moon } from "lucide-react";

import type { QuietView } from "@/lib/api-types";
import { quietLabel } from "@/lib/quiet-api";

/** The grey "this one is quiet" pill: what a snooze or a leave-alone says, with a moon. */
export function QuietPill({ quiet }: { quiet: QuietView }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-stop-bg px-3 py-1 text-[13px] font-bold text-stop">
      <Moon aria-hidden className="size-3.5" />
      {quietLabel(quiet, new Date())}
    </span>
  );
}
