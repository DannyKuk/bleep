"use client";

import { Brain, Loader2, Plus, Trash2, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import type { FactBody, ServiceFact } from "@/lib/api-types";
import { formatDateTime } from "@/lib/format";

const MAX_FACT = 200; // the API's limit (FACT_CHARS)

const why = (error: unknown) => (error instanceof Error ? error.message : String(error));

function factsPath(serviceId: string): string {
  return `/api/services/${encodeURIComponent(serviceId)}/facts`;
}

/** Where a fact came from: a person, or the report of an incident (linked). */
function FactOrigin({ fact }: { fact: ServiceFact }) {
  const when = <time dateTime={fact.created_at}>{formatDateTime(fact.created_at)}</time>;
  if (fact.source === "human") return <>Added by you, {when}</>;
  return (
    <>
      Learned from{" "}
      {fact.incident_id ? (
        <Link
          href={`/incidents/${fact.incident_id}`}
          className="underline underline-offset-2 hover:text-ink"
        >
          an incident&apos;s report
        </Link>
      ) : (
        "a report"
      )}
      , {when}
    </>
  );
}

/** The service's facts, each with where it came from and a delete button. */
export function FactList({
  facts,
  deleting,
  onDelete,
}: {
  facts: ServiceFact[];
  /** The id of the fact being deleted, if any. */
  deleting: number | null;
  onDelete: (fact: ServiceFact) => void;
}) {
  if (facts.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        No facts yet. Add one here, or tell Bleep during an incident, such as &ldquo;always redeploy
        rather than restart&rdquo;.
      </p>
    );
  }
  return (
    <ul aria-label="Facts" className="flex flex-col divide-y divide-line">
      {facts.map((fact) => (
        <li key={fact.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-sm break-words">{fact.text}</span>
            <span className="text-xs text-ink-2">
              <FactOrigin fact={fact} />
            </span>
          </div>
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Delete the fact: ${fact.text}`}
            title="Delete this fact"
            disabled={deleting !== null}
            onClick={() => onDelete(fact)}
          >
            {deleting === fact.id ? (
              <Loader2 aria-hidden className="animate-spin" />
            ) : (
              <Trash2 aria-hidden />
            )}
          </Button>
        </li>
      ))}
    </ul>
  );
}

/**
 * What Bleep remembers about a service: facts a person added and facts learned from reports.
 * Gather hands every one of them to the agent on the service's next incident.
 */
export function MemoryPanel({ serviceId }: { serviceId: string }) {
  const [facts, setFacts] = useState<ServiceFact[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const inputId = useId();

  useEffect(() => {
    const controller = new AbortController();
    apiFetch<ServiceFact[]>(factsPath(serviceId), { signal: controller.signal })
      .then(setFacts)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setFailure(`Could not load the facts: ${why(error)}`);
      });
    return () => controller.abort();
  }, [serviceId]);

  async function add() {
    const body: FactBody = { text: text.trim() };
    setAdding(true);
    setFailure(null);
    try {
      const fact = await apiFetch<ServiceFact>(factsPath(serviceId), {
        method: "POST",
        body: JSON.stringify(body),
      });
      setFacts((known) => [...(known ?? []), fact]);
      setText("");
    } catch (error) {
      setFailure(`Could not add the fact: ${why(error)}`);
    } finally {
      setAdding(false);
    }
  }

  async function remove(fact: ServiceFact) {
    setDeleting(fact.id);
    setFailure(null);
    try {
      await apiFetch<void>(`${factsPath(serviceId)}/${fact.id}`, { method: "DELETE" });
      setFacts((known) => (known ?? []).filter((kept) => kept.id !== fact.id));
    } catch (error) {
      setFailure(`Could not delete the fact: ${why(error)}`);
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-2 text-xs text-ink-2">
        <Brain aria-hidden className="size-3.5" />
        Bleep reads these on every incident of this service.
      </p>
      {facts === null && !failure ? (
        <p role="status" className="flex items-center gap-2 text-sm text-ink-2">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          Loading the facts
        </p>
      ) : (
        <FactList facts={facts ?? []} deleting={deleting} onDelete={(fact) => void remove(fact)} />
      )}
      <form
        className="flex flex-col gap-1.5"
        onSubmit={(submitted) => {
          submitted.preventDefault();
          if (text.trim()) void add();
        }}
      >
        <label htmlFor={inputId} className="text-xs text-ink-2">
          Something Bleep should always know about this service
        </label>
        <div className="flex gap-2">
          <Input
            id={inputId}
            value={text}
            maxLength={MAX_FACT}
            onChange={(changed) => setText(changed.target.value)}
          />
          <Button type="submit" size="default" variant="outline" disabled={!text.trim() || adding}>
            {adding ? <Loader2 aria-hidden className="animate-spin" /> : <Plus aria-hidden />}
            Add
          </Button>
        </div>
      </form>
      {failure && (
        <p role="alert" className="flex items-center gap-2 text-xs text-down">
          <TriangleAlert aria-hidden className="size-3.5" />
          {failure}
        </p>
      )}
    </div>
  );
}
