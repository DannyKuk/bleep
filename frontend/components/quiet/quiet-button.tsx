"use client";

import { ChevronDown, Moon } from "lucide-react";

import { QuietMenu } from "@/components/quiet/quiet-menu";
import { useTimeZone } from "@/components/time-zone";
import { Button } from "@/components/ui/button";
import type { QuietView } from "@/lib/api-types";
import { quietLabel } from "@/lib/quiet-api";
import { cn } from "@/lib/utils";

// Tag-sized, so it sits level with the health badge beside a page's title; a touch screen gets a
// 44 px tap area around it.
const CHIP =
  "relative h-7 gap-1 px-3 text-[13px] pointer-coarse:after:absolute pointer-coarse:after:-inset-2 pointer-coarse:after:content-[''] [&_svg]:size-3.5";

/** A page title's quiet control: "Quiet" while watched; while quiet, the grey pill opens the menu. */
export function QuietButton({
  target,
  quiet,
  onChanged,
}: {
  target: { scope: "service" | "host"; id: string; name: string };
  quiet: QuietView | null;
  onChanged: () => void;
}) {
  const zone = useTimeZone();
  const label = quiet ? quietLabel(quiet, new Date(), zone) : null;
  return (
    <QuietMenu
      target={target}
      quiet={quiet}
      onChanged={onChanged}
      trigger={
        label ? (
          <Button
            variant="ghost"
            aria-label={`Quiet ${target.name}: ${label}`}
            className={cn(CHIP, "bg-stop-bg text-stop hover:bg-stop-bg/70")}
          >
            <Moon aria-hidden />
            {label}
            <ChevronDown aria-hidden />
          </Button>
        ) : (
          <Button variant="outline" aria-label={`Quiet ${target.name}`} className={CHIP}>
            <Moon aria-hidden />
            Quiet
          </Button>
        )
      }
    />
  );
}
