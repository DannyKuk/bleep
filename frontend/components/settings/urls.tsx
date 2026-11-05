"use client";

import { Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { SourceNote } from "@/components/settings/rules";
import { SaveError, useSaving } from "@/components/settings/save-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { UrlCheck } from "@/lib/settings-types";

const URL_LABELS = {
  urls: "URL",
  url: "URL",
  expect_status: "Expected status",
  body_contains: "Body contains",
};

interface Row {
  url: string;
  status: string;
  contains: string;
}

const toRow = (check: UrlCheck): Row => ({
  url: check.url,
  status: String(check.expect_status),
  contains: check.body_contains ?? "",
});

/** The row as the API takes it. A status that is not a number goes as typed, for the API to
 * refuse with its reason. */
function toCheck(row: Row): UrlCheck {
  const status = row.status.trim();
  return {
    url: row.url.trim(),
    expect_status: (status && Number.isInteger(Number(status)) ? Number(status) : status) as number,
    body_contains: row.contains.trim() || null,
  };
}

/** The URLs Bleep checks for a service: address, expected status and an optional text. */
export function UrlEditor({
  service,
  urls,
  overridden,
  onSave,
  onReset,
  onClose,
}: {
  service: string;
  urls: UrlCheck[];
  overridden: boolean;
  onSave: (urls: UrlCheck[]) => Promise<unknown>;
  onReset: () => Promise<unknown>;
  /** Set when the URLs are opened for editing: Cancel always shows, and a save closes them. */
  onClose?: () => void;
}) {
  const [rows, setRows] = useState(() => urls.map(toRow));
  const { saving, busy, problems, run } = useSaving(URL_LABELS);
  const checks = rows.map(toCheck);
  const [blank, setBlank] = useState<number[]>([]);
  const dirty = JSON.stringify(checks) !== JSON.stringify(urls.map((u) => toCheck(toRow(u))));

  async function done(save: () => Promise<unknown>) {
    if (await run(save)) onClose?.();
  }

  function change(index: number, field: keyof Row, value: string) {
    setRows(rows.map((row, at) => (at === index ? { ...row, [field]: value } : row)));
  }

  /** A row without a URL is shown as invalid, never dropped from the list on its own. */
  function save() {
    const empty = rows.flatMap((row, at) => (row.url.trim() ? [] : [at]));
    setBlank(empty);
    if (empty.length === 0) void done(() => onSave(checks));
  }

  return (
    <div className="flex flex-col gap-2">
      <SourceNote overridden={overridden} disabled={busy} onReset={() => void done(onReset)} />
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No URLs are checked.</p>
      ) : (
        <ul aria-label={`URLs for ${service}`} className="flex flex-col gap-2">
          {rows.map((row, index) => (
            <li key={index} className="flex flex-wrap gap-2 sm:flex-nowrap">
              <Input
                aria-label={`URL ${index + 1}`}
                placeholder="https://shop.example.com/health"
                value={row.url}
                aria-invalid={blank.includes(index) || undefined}
                className="min-w-0 basis-full font-mono aria-invalid:border-health-down sm:basis-auto"
                onChange={(changed) => change(index, "url", changed.target.value)}
              />
              <Input
                aria-label={`Expected status ${index + 1}`}
                inputMode="numeric"
                value={row.status}
                className="w-20 shrink-0"
                onChange={(changed) => change(index, "status", changed.target.value)}
              />
              <Input
                aria-label={`Body contains ${index + 1}`}
                placeholder="Body contains (optional)"
                value={row.contains}
                className="min-w-0 flex-1"
                onChange={(changed) => change(index, "contains", changed.target.value)}
              />
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`Remove ${row.url || `URL ${index + 1}`}`}
                onClick={() => {
                  setRows(rows.filter((_, at) => at !== index));
                  setBlank([]);
                }}
              >
                <Trash2 aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setRows([...rows, { url: "", status: "200", contains: "" }])}
        >
          <Plus aria-hidden />
          Add a URL
        </Button>
        {dirty && (
          <Button type="button" size="sm" disabled={busy} onClick={save}>
            {saving && <Loader2 aria-hidden className="animate-spin" />}
            Save
          </Button>
        )}
        {(dirty || onClose) && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={saving}
            onClick={() => {
              setRows(urls.map(toRow));
              setBlank([]);
              onClose?.();
            }}
          >
            Cancel
          </Button>
        )}
      </div>
      <SaveError
        problems={
          blank.length > 0
            ? blank.map((at) => `URL ${at + 1}: enter an address, or remove the row`)
            : problems
        }
      />
    </div>
  );
}
