"use client";

import { Loader2, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { SaveError, useSaving } from "@/components/settings/save-error";
import { SecretFields } from "@/components/settings/secret-field";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";
import type { SecretActions, SecretView } from "@/lib/secrets-api";

/** What the page hands a panel so it can show the secrets that belong to it. */
export interface PanelSecrets {
  views: SecretView[];
  actions: SecretActions;
}

export interface Field {
  name: string;
  label: string;
  /** The variable that sets it in the environment. */
  env: string;
  /** A fixed choice, shown as a select. */
  options?: { value: string; label: string }[];
  /** A number: sent as one when it is, as typed (for the API to refuse) when it is not. */
  number?: boolean;
  hint?: string;
  /** Shown, and counted in a save, only while this holds of the draft; hidden keeps its stored value. */
  visibleWhen?: (draft: Record<string, string>) => boolean;
}

export interface Group {
  title?: string;
  fields: Field[];
}

/** What a save is about to change, for a form that wants a confirmation first. */
export interface Change {
  /** The editable fields whose draft differs from the value in force. */
  changed: string[];
  draft: Record<string, string>;
  /** The save puts back the layer below: every override is cleared. */
  reset: boolean;
}

/** The fields of `section` the environment decides, from the view's `from_env`. */
export const envFields = (fromEnv: string[], section: string): string[] =>
  fromEnv
    .filter((name) => name.startsWith(`${section}.`))
    .map((name) => name.slice(section.length + 1));

/** The secrets in `names` that the page has views for, as rows (an address before its token). */
export function SecretRows({
  secrets,
  names,
}: {
  secrets: PanelSecrets | undefined;
  names: readonly string[];
}) {
  const views = (secrets?.views ?? []).filter((view) => names.includes(view.name));
  if (!secrets || views.length === 0) return null;
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <SecretFields views={views} actions={secrets.actions} />
    </div>
  );
}

/** Says a saved change waits for the next start; shown only while the API lists the section. */
export function RestartNote({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="flex items-start gap-2 text-sm text-health-degraded">
      <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/**
 * Whether the draft of `field` is an edit. A number is compared as a number ("0.30" is "0.3"),
 * and emptying a field no override sets changes nothing: there is nothing to put back.
 */
function isEdit(field: Field, draft: string, inForce: string, overridden: boolean): boolean {
  const text = draft.trim();
  if (text === inForce) return false;
  if (text === "") return overridden;
  return !(field.number && inForce !== "" && Number(text) === Number(inForce));
}

/** A typed value as a number; anything else goes as typed, for the API to refuse with why. */
const parsed = (text: string): number | string => {
  const value = Number(text);
  return text !== "" && Number.isFinite(value) ? value : text;
};

function FieldRow({
  field,
  value,
  draft,
  fromEnv,
  onChange,
}: {
  field: Field;
  value: string;
  draft: string;
  fromEnv: boolean;
  onChange: (value: string) => void;
}) {
  const t = useT().settings.section;
  const id = useId();
  const described = field.hint ? `${id}-hint` : undefined;
  const head = (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      {fromEnv ? (
        <span className="text-xs text-muted-foreground">{field.label}</span>
      ) : (
        <label htmlFor={id} className="text-xs text-muted-foreground">
          {field.label}
        </label>
      )}
      <code className="font-mono text-xs break-all text-muted-foreground">{field.env}</code>
    </div>
  );
  if (fromEnv) {
    return (
      <div role="group" aria-label={field.label} className="flex flex-col gap-1">
        {head}
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{t.inEnv}</Badge>
          {value && <code className="font-mono text-sm break-all">{value}</code>}
        </div>
        <p className="text-xs text-muted-foreground">{t.changeInEnv}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {head}
      {field.options ? (
        <select
          id={id}
          value={draft}
          aria-describedby={described}
          onChange={(changed) => onChange(changed.target.value)}
          className="h-11 w-full max-w-sm rounded-field border border-line bg-surface px-3 text-[15px] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <Input
          id={id}
          value={draft}
          aria-describedby={described}
          inputMode={field.number ? "decimal" : undefined}
          autoComplete="off"
          spellCheck={false}
          className={field.number ? "max-w-32" : "max-w-xl font-mono"}
          onChange={(changed) => onChange(changed.target.value)}
        />
      )}
      {field.hint && (
        <p id={described} className="text-xs text-muted-foreground">
          {field.hint}
        </p>
      )}
    </div>
  );
}

/** Fields that show the default of the choice made in another field's draft (#517). */
export interface Follow {
  /** The select whose draft decides: the provider. */
  on: string;
  /** Per choice of `on`, the default each following field shows. */
  defaults: Record<string, Record<string, string>>;
}

/**
 * The form of one settings section (models, embeddings, tracing). `values` are those in force,
 * as text; `overrides` are what the database holds, which a save sends again with the changed
 * fields on top (an emptied field is left out, so the layer below shows). A field in `fromEnv`
 * has no input: the environment wins, and the API refuses a change to it. `confirm`, when it
 * returns a text, makes a save ask first, inline; the save goes only on the second click.
 * `children` come after the fields, before the buttons. `follow` makes some fields show the
 * default of the choice made in another field's draft, while no override sets them and the person
 * has not typed in them; such a field is no edit, so a save leaves it unset. `onDraft` hears the
 * draft as it is shown.
 */
export function SectionForm({
  groups,
  values,
  overrides,
  fromEnv,
  what,
  confirm,
  confirmLabel,
  follow,
  onDraft,
  onSave,
}: {
  groups: Group[];
  values: Record<string, string>;
  overrides: Record<string, unknown>;
  fromEnv: string[];
  /** Names the section in the buttons: "Save the models". */
  what: string;
  confirm?: (change: Change) => string | null;
  confirmLabel?: string;
  follow?: Follow;
  onDraft?: (draft: Record<string, string>) => void;
  onSave: (body: Record<string, unknown>) => Promise<unknown>;
}) {
  const { section: t, save: saveWord, cancel } = useT().settings;
  const fields = groups.flatMap((group) => group.fields);
  const labels = Object.fromEntries(fields.map((field) => [field.name, field.label]));
  const [typed, setTyped] = useState<Set<string>>(new Set());
  const [chosen, setDraft] = useState(values);
  const follows = (name: string): boolean =>
    follow !== undefined &&
    chosen[follow.on] !== values[follow.on] &&
    !(name in overrides) &&
    !typed.has(name) &&
    follow.defaults[chosen[follow.on]]?.[name] !== undefined;
  const draft = Object.fromEntries(
    Object.entries(chosen).map(([name, text]) => [
      name,
      follow && follows(name) ? follow.defaults[chosen[follow.on]][name] : text,
    ]),
  );
  const shown = (field: Field) => field.visibleWhen?.(draft) ?? true;
  const draftKey = JSON.stringify(draft);
  useEffect(() => onDraft?.(draft), [draftKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const [asking, setAsking] = useState<{ body: Record<string, unknown>; text: string } | null>(
    null,
  );
  const { saving, busy, problems, run } = useSaving(labels);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const saveButton = useRef<HTMLButtonElement>(null);
  const cancelled = useRef(false);
  const questionId = useId();
  useEffect(() => {
    if (asking) confirmButton.current?.focus();
    else if (cancelled.current) saveButton.current?.focus();
    cancelled.current = false;
  }, [asking]);
  const editable = fields.filter((field) => !fromEnv.includes(field.name));
  const changed = editable.filter(
    (field) =>
      shown(field) &&
      !follows(field.name) &&
      isEdit(field, draft[field.name], values[field.name], field.name in overrides),
  );
  const overridden = Object.keys(overrides).length > 0;

  function body(): Record<string, unknown> {
    const update = { ...overrides };
    for (const field of changed) {
      const text = chosen[field.name].trim();
      if (text === "") delete update[field.name];
      else update[field.name] = field.number ? parsed(text) : text;
    }
    return update;
  }

  async function save(update: Record<string, unknown>) {
    await run(() => onSave(update));
    setAsking(null);
  }

  function submit(update: Record<string, unknown>, reset: boolean) {
    const text = confirm?.({ changed: changed.map((field) => field.name), draft, reset });
    if (text) setAsking({ body: update, text });
    else void save(update);
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(submitted) => {
        submitted.preventDefault();
        if (!asking) submit(body(), false);
      }}
    >
      {groups.map((group, at) => {
        const rows = group.fields.filter(shown).map((field) => (
          <FieldRow
            key={field.name}
            field={field}
            value={values[field.name]}
            draft={draft[field.name]}
            fromEnv={fromEnv.includes(field.name)}
            onChange={(value) => {
              setAsking(null);
              setTyped(new Set(typed).add(field.name));
              setDraft({ ...chosen, [field.name]: value });
            }}
          />
        ));
        return group.title ? (
          <fieldset key={at} className="flex flex-col gap-3 border-t border-line pt-3">
            <legend className="pr-2 text-sm font-bold">{group.title}</legend>
            {rows}
          </fieldset>
        ) : (
          <div key={at} className="flex flex-col gap-3">
            {rows}
          </div>
        );
      })}
      {asking ? (
        <div
          role="status"
          aria-label={t.confirm}
          className="flex flex-col gap-2 rounded-row bg-soft p-3"
        >
          <p id={questionId} className="text-sm">
            {asking.text}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              wrap
              ref={confirmButton}
              type="button"
              size="sm"
              aria-describedby={questionId}
              disabled={busy}
              onClick={() => void save(asking.body)}
            >
              {saving && <Loader2 aria-hidden className="animate-spin" />}
              {confirmLabel ?? saveWord}
            </Button>
            <Button
              wrap
              type="button"
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={() => {
                cancelled.current = true;
                setAsking(null);
              }}
            >
              {cancel}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            wrap
            ref={saveButton}
            type="submit"
            size="sm"
            disabled={busy || changed.length === 0}
          >
            {saving && <Loader2 aria-hidden className="animate-spin" />}
            {what}
          </Button>
          {changed.length > 0 && (
            <Button
              wrap
              type="button"
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={() => {
                setTyped(new Set());
                setDraft(values);
              }}
            >
              {cancel}
            </Button>
          )}
          {overridden && (
            <Button
              wrap
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => submit({}, true)}
            >
              {t.defaults}
            </Button>
          )}
        </div>
      )}
      <SaveError problems={problems} />
    </form>
  );
}
