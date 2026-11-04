import * as React from "react";

import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-11 w-full rounded-field border border-line bg-surface px-4 text-[15px] placeholder:text-ink-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
