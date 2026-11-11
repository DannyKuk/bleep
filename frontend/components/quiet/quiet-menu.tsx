"use client";

import { Moon } from "lucide-react";
import { useRef, useState, type ReactElement } from "react";

import { SaveError, problemsOf } from "@/components/settings/save-error";
import { useTimeZone } from "@/components/preferences";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { QuietView } from "@/lib/api-types";
import { type Messages, useLanguage, useT } from "@/lib/i18n";
import type { Language } from "@/lib/settings-types";
import type { Zone } from "@/lib/zone";
import {
  clearQuiet,
  morningLabel,
  quietLabel,
  setQuiet,
  snoozeUntil,
  type QuietBody,
  type QuietTarget,
  type SnoozeChoice,
} from "@/lib/quiet-api";

const snoozes = (
  now: Date,
  zone: Zone,
  language: Language,
  t: Messages,
): { choice: SnoozeChoice; label: string }[] => [
  { choice: "1h", label: t.quiet.forHour },
  { choice: "4h", label: t.quiet.forHours },
  { choice: "morning", label: morningLabel(now, zone, language) },
];

interface QuietMenuProps {
  target: QuietTarget;
  quiet: QuietView | null;
  onChanged: () => void;
  trigger?: ReactElement;
  /** Extra classes for the default moon button. */
  className?: string;
}

/** The moon menu: snooze the target, leave it alone, or watch it again. */
// As tall as a health badge beside it (28 px); a touch screen gets a 44 px tap area around it.
// The hover is a tint of the text colour, since the ghost button's `soft` is a healthy tile's own
// background.
const MOON =
  "relative size-7 shrink-0 hover:bg-ink/10 data-popup-open:bg-ink/10 [&_svg]:size-3.5 pointer-coarse:after:absolute pointer-coarse:after:-inset-2 pointer-coarse:after:content-['']";

export function QuietMenu({ target, quiet, onChanged, trigger, className }: QuietMenuProps) {
  const [open, setOpen] = useState(false);
  const busy = useRef(false);
  const [problems, setProblems] = useState<string[]>([]);
  const own = quiet !== null && quiet.scope === target.scope;
  const throughHost = quiet !== null && !own;
  const zone = useTimeZone();
  const language = useLanguage();
  const t = useT();
  const words = t.quiet;

  async function apply(change: () => Promise<unknown>) {
    if (busy.current) return;
    busy.current = true;
    setProblems([]);
    try {
      await change();
    } catch (error) {
      setProblems(problemsOf(error));
      return;
    } finally {
      busy.current = false;
    }
    setOpen(false);
    onChanged();
  }

  const put = (body: QuietBody) => apply(() => setQuiet(target, body));

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setProblems([]);
      }}
    >
      <DropdownMenuTrigger
        render={
          trigger ?? (
            <Button
              variant="ghost"
              size="icon"
              aria-label={words.quietName(target.name)}
              className={cn(MOON, className)}
            >
              <Moon aria-hidden />
            </Button>
          )
        }
      />
      <DropdownMenuContent>
        <DropdownMenuGroup>
          <DropdownMenuLabel>
            {quiet ? quietLabel(quiet, new Date(), zone, language) : words.snoozeName(target.name)}
            {throughHost && words.throughHost}
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        {own && (
          <DropdownMenuItem closeOnClick={false} onClick={() => apply(() => clearQuiet(target))}>
            {words.watchAgain}
          </DropdownMenuItem>
        )}
        {quiet && <DropdownMenuSeparator />}
        {snoozes(new Date(), zone, language, t).map(({ choice, label }) => (
          <DropdownMenuItem
            key={choice}
            closeOnClick={false}
            onClick={() =>
              put({ mode: "snooze", until: snoozeUntil(choice, new Date(), zone).toISOString() })
            }
          >
            {label}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem closeOnClick={false} onClick={() => put({ mode: "leave_alone" })}>
          {words.leaveAlone}
          <span className="text-xs font-normal text-ink-2">{words.leaveAloneHint}</span>
        </DropdownMenuItem>
        {problems.length > 0 && (
          <div className="px-2.5 pt-2 pb-1">
            <SaveError problems={problems} />
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
