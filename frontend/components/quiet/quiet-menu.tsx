"use client";

import { Moon } from "lucide-react";
import { useState, type ReactElement } from "react";

import { SaveError, problemsOf } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { QuietView } from "@/lib/api-types";
import {
  clearQuiet,
  quietLabel,
  setQuiet,
  snoozeUntil,
  type QuietBody,
  type QuietTarget,
  type SnoozeChoice,
} from "@/lib/quiet-api";

const SNOOZES: { choice: SnoozeChoice; label: string }[] = [
  { choice: "1h", label: "For 1 hour" },
  { choice: "4h", label: "For 4 hours" },
  { choice: "morning", label: "Until tomorrow 8:00" },
];

interface QuietMenuProps {
  target: QuietTarget;
  quiet: QuietView | null;
  onChanged: () => void;
  trigger?: ReactElement;
}

/** The moon menu: snooze the target, leave it alone, or watch it again. */
export function QuietMenu({ target, quiet, onChanged, trigger }: QuietMenuProps) {
  const [open, setOpen] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const own = quiet !== null && quiet.scope === target.scope;
  const throughHost = quiet !== null && !own;

  async function apply(change: () => Promise<unknown>) {
    setProblems([]);
    try {
      await change();
    } catch (error) {
      setProblems(problemsOf(error));
      return;
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
            <Button variant="ghost" size="icon" aria-label={`Quiet ${target.name}`}>
              <Moon aria-hidden />
            </Button>
          )
        }
      />
      <DropdownMenuContent>
        {quiet && (
          <DropdownMenuLabel>
            {quietLabel(quiet, new Date())}
            {throughHost && " (set on its host)"}
          </DropdownMenuLabel>
        )}
        {own && (
          <DropdownMenuItem closeOnClick={false} onClick={() => apply(() => clearQuiet(target))}>
            Watch again
          </DropdownMenuItem>
        )}
        {quiet && <DropdownMenuSeparator />}
        {SNOOZES.map(({ choice, label }) => (
          <DropdownMenuItem
            key={choice}
            closeOnClick={false}
            onClick={() =>
              put({ mode: "snooze", until: snoozeUntil(choice, new Date()).toISOString() })
            }
          >
            {label}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem closeOnClick={false} onClick={() => put({ mode: "leave_alone" })}>
          Leave it alone
          <span className="text-xs font-normal text-ink-2">until you switch it back</span>
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
