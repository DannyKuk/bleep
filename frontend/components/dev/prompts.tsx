"use client";

import { FileText, Loader2, RotateCcw, Save } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { useDraft } from "@/components/dev/models";
import { Failed, Loading, why } from "@/components/dev/status";
import { useTimeZone } from "@/components/time-zone";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import type { PromptOverrideBody, PromptView } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";
import { isStale } from "@/lib/settings-api";

const MAX_PROMPT = 20_000; // the API's limit (PROMPT_CHARS)

const NOT_SAVED = "Not saved: this prompt changed elsewhere since this page read it.";
export const PROMPT_READ_AGAIN = `${NOT_SAVED} It has been read again and your edit kept on top of it; check it and save again if your change is still needed.`;
export const promptRereadFailed = (error: unknown) =>
  `${NOT_SAVED} Reading it again failed (${why(error)}); reload the page before you change anything else.`;

const promptPath = (name: string) =>
  `/api/dev/prompts/${name.split("/").map(encodeURIComponent).join("/")}`;

/**
 * One prompt's text, edited as an override; the file stays as it is. A save or reset sends the
 * prompt's `version`; after a 409 it reads the prompts again (`onStale`) and says so, keeps the
 * person's edit (an untouched text takes the new one) and is never retried.
 */
export function PromptEditor({
  prompt,
  onSaved,
  onStale,
}: {
  prompt: PromptView;
  onSaved: (prompt: PromptView) => void;
  /** Read the prompts again; throws when that read fails. */
  onStale: () => Promise<void>;
}) {
  const zone = useTimeZone();
  const inForce = prompt.override ?? prompt.file_text;
  const [draft, setDraft] = useDraft({ text: inForce });
  const text = draft.text;
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const id = useId();

  async function send(init: RequestInit, failed: string) {
    setBusy(true);
    setFailure(null);
    try {
      const saved = await apiFetch<PromptView>(promptPath(prompt.name), {
        ...init,
        headers: { "if-match": `"${prompt.version}"` },
      });
      setDraft({ text: saved.override ?? saved.file_text });
      onSaved(saved);
    } catch (error) {
      if (isStale(error)) {
        try {
          await onStale();
          setFailure(PROMPT_READ_AGAIN);
        } catch (failed) {
          setFailure(promptRereadFailed(failed));
        }
      } else {
        setFailure(`${failed}: ${why(error)}`);
      }
    } finally {
      setBusy(false);
    }
  }

  const body: PromptOverrideBody = { text };
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        void send({ method: "PUT", body: JSON.stringify(body) }, "Could not save the override");
      }}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {prompt.override !== null && prompt.overridden_at ? (
          <span>
            Overridden{" "}
            <time dateTime={prompt.overridden_at}>
              {formatDateTime(prompt.overridden_at, zone)}
            </time>
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <FileText aria-hidden className="size-3.5" />
            Read from <span className="font-mono">prompts/{prompt.name}.md</span>
          </span>
        )}
        <span aria-label="Placeholders" className="flex flex-wrap gap-1">
          {prompt.placeholders.length === 0
            ? "No placeholders"
            : prompt.placeholders.map((name) => (
                <Badge key={name} className="font-mono">{`{${name}}`}</Badge>
              ))}
        </span>
      </div>
      <label htmlFor={id} className="sr-only">
        Text of {prompt.name}
      </label>
      <textarea
        id={id}
        value={text}
        maxLength={MAX_PROMPT}
        rows={14}
        spellCheck={false}
        onChange={(changed) => setDraft({ text: changed.target.value })}
        className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      />
      <p className="text-xs text-muted-foreground">
        Use exactly the file&apos;s placeholders; write a literal brace as {"{{"} or {"}}"}.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="outline" disabled={busy || !text.trim() || text === inForce}>
          {busy ? <Loader2 aria-hidden className="animate-spin" /> : <Save aria-hidden />}
          Save the override
        </Button>
        {prompt.override !== null && (
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => void send({ method: "DELETE" }, "Could not reset the prompt")}
          >
            <RotateCcw aria-hidden />
            Reset to the file
          </Button>
        )}
      </div>
      {failure && <Failed>{failure}</Failed>}
    </form>
  );
}

/** Every prompt Bleep loads, one at a time: pick it, edit it, save it as an override. */
export function PromptsPanel() {
  const [prompts, setPrompts] = useState<PromptView[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const id = useId();

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<PromptView[]>("/api/dev/prompts", { signal: controller.signal })
      .then(setPrompts)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(`Could not load the prompts: ${why(error)}`);
      });
    return () => controller.abort();
  }, []);

  async function readAgain() {
    setPrompts(await apiFetch<PromptView[]>("/api/dev/prompts"));
  }

  if (failure) return <Failed>{failure}</Failed>;
  if (!prompts) return <Loading>Loading the prompts</Loading>;
  const selected = chosen ?? prompts[0]?.name ?? "";
  const prompt = prompts.find((p) => p.name === selected);
  const overridden = prompts.filter((p) => p.override !== null).length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={id} className="text-xs text-muted-foreground">
          Prompt ({overridden} of {prompts.length} overridden)
        </label>
        <select
          id={id}
          value={selected}
          onChange={(changed) => setChosen(changed.target.value)}
          className="h-9 w-full rounded-md border border-input bg-background px-3 font-mono text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {prompts.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
              {p.override !== null ? " (overridden)" : ""}
            </option>
          ))}
        </select>
      </div>
      {prompt && (
        <PromptEditor
          key={prompt.name}
          prompt={prompt}
          onSaved={(saved) =>
            setPrompts((known) => (known ?? []).map((p) => (p.name === saved.name ? saved : p)))
          }
          onStale={readAgain}
        />
      )}
    </div>
  );
}
