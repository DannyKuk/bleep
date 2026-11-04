import * as React from "react";

import { cn } from "@/lib/utils";

/** A small neutral label (kind, source). Health uses HealthBadge, which carries colour. */
export function Badge({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-line bg-soft px-2.5 py-0.5 text-xs font-bold text-ink-2",
        className,
      )}
      {...props}
    />
  );
}
