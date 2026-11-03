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
}: {
  service: string;
  urls: UrlCheck[];
  overridden: boolean;
  onSave: (urls: UrlCheck[]) => Promise<unknown>;
  onReset: () => Promise<unknown>;
}) {
  const [rows, setRows] = useState(() => urls.map(toRow));
  const { saving, problems, run } = useSaving(URL_LABELS);
  const checks = rows.filter((row) => row.url.trim()).map(toCheck);
  const dirty = JSON.stringify(checks) !== JSON.stringify(urls.map((u) => toCheck(toRow(u))));

  function change(index: number, field: keyof Row, value: string) {
    setRows(rows.map((row, at) => (at === index ? { ...row, [field]: value } : row)));
  }

  return (
    <div className="flex flex-col gap-2">
      <SourceNote overridden={overridden} disabled={saving} onReset={() => void run(onReset)} />
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
                className="min-w-0 basis-full font-mono sm:basis-auto"
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
                onClick={() => setRows(rows.filter((_, at) => at !== index))}
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
          <>
            <Button
              type="button"
              size="sm"
              disabled={saving}
              onClick={() => void run(() => onSave(checks))}
            >
              {saving && <Loader2 aria-hidden className="animate-spin" />}
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={() => setRows(urls.map(toRow))}
            >
              Cancel
            </Button>
          </>
        )}
      </div>
      <SaveError problems={problems} />
    </div>
  );
}
