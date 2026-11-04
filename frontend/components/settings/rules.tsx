"use client";

import { Loader2, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Where the values in force come from, and the way back to the hosts file's. */
export function SourceNote({
  overridden,
  onReset,
  disabled,
}: {
  overridden: boolean;
  onReset: () => void;
  disabled?: boolean;
}) {
  if (!overridden)
    return <span className="text-xs text-muted-foreground">From the hosts file</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
      Changed here
      <button
        type="button"
        disabled={disabled}
        onClick={onReset}
        className="underline underline-offset-2 hover:text-foreground disabled:opacity-50"
      >
        Use the hosts file&apos;s
      </button>
    </span>
  );
}

/**
 * A list of short texts (rules, container names) edited as a draft: change, add and remove
 * entries, then save the whole list. `onSave` throws on a refusal, which is shown under the list.
 */
export function ListEditor({
  label,
  items,
  onSave,
  addLabel,
  emptyText,
  mono = false,
  labels,
  overridden,
  onReset,
}: {
  /** Names the list and its inputs ("Rules for shop"). */
  label: string;
  items: string[];
  onSave: (items: string[]) => Promise<unknown>;
  addLabel: string;
  emptyText: string;
  mono?: boolean;
  /** Field names in the API's errors to the words shown (`{ rules: "Rule" }`). */
  labels?: Record<string, string>;
  /** Set for a list the hosts file has too: whether the database's list replaces it. */
  overridden?: boolean;
  onReset?: () => Promise<unknown>;
}) {
  const [draft, setDraft] = useState(items);
  const [adding, setAdding] = useState("");
  const { saving, busy, problems, run } = useSaving(labels);
  const addId = useId();
  const cleaned = draft.map((item) => item.trim()).filter(Boolean);
  const dirty = JSON.stringify(cleaned) !== JSON.stringify(items);

  function add() {
    if (!adding.trim()) return;
    setDraft([...draft, adding.trim()]);
    setAdding("");
  }

  return (
    <div className="flex flex-col gap-2">
      {overridden !== undefined && onReset && (
        <SourceNote overridden={overridden} disabled={busy} onReset={() => void run(onReset)} />
      )}
      {draft.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ul aria-label={label} className="flex flex-col gap-1.5">
          {draft.map((item, index) => (
            <li key={index} className="flex gap-2">
              <Input
                aria-label={`${label}: entry ${index + 1}`}
                value={item}
                className={cn(mono && "font-mono")}
                onChange={(changed) =>
                  setDraft(draft.map((old, at) => (at === index ? changed.target.value : old)))
                }
              />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`Remove ${item || `entry ${index + 1}`}`}
                onClick={() => setDraft(draft.filter((_, at) => at !== index))}
              >
                <Trash2 aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <label htmlFor={addId} className="sr-only">
          {addLabel}
        </label>
        <Input
          id={addId}
          placeholder={addLabel}
          value={adding}
          className={cn(mono && "font-mono")}
          onChange={(changed) => setAdding(changed.target.value)}
          onKeyDown={(pressed) => {
            if (pressed.key === "Enter") {
              pressed.preventDefault();
              add();
            }
          }}
        />
        <Button type="button" variant="outline" disabled={!adding.trim()} onClick={add}>
          <Plus aria-hidden />
          Add
        </Button>
      </div>
      {dirty && (
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => void run(() => onSave(cleaned))}
          >
            {saving && <Loader2 aria-hidden className="animate-spin" />}
            Save
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={saving}
            onClick={() => setDraft(items)}
          >
            Cancel
          </Button>
        </div>
      )}
      <SaveError problems={problems} />
    </div>
  );
}
