"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { CodeText } from "@/components/code-text";
import { useTimeZone } from "@/components/preferences";
import { Alert } from "@/components/settings/loaded";
import { Fact } from "@/components/settings/secret";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { fetchRepoDocs, startRepoRefresh, type RepoDocsState } from "@/lib/knowledge-api";

/** How often the page asks whether a read is over. */
export const POLL_MS = 2000;

/**
 * The docs of the repos services link, as Bleep holds them: when they were last read and the
 * chunks each repo has. "Re-read docs" starts a read now, for a person who fixed their docs after
 * an incident and does not want to wait for the daily read. A read can take over a minute, longer
 * than a request may, so the press only starts it and the page asks again until it is over.
 */
export function RepoDocs({
  initial,
  pollMs = POLL_MS,
}: {
  initial: RepoDocsState;
  pollMs?: number;
}) {
  const t = useT().settings.docs;
  const zone = useTimeZone();
  const language = useLanguage();
  const [state, setState] = useState<RepoDocsState>(initial);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!state.reading) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchRepoDocs(controller.signal)
        .then(setState)
        .catch((failed: unknown) => {
          if (controller.signal.aborted) return;
          setError(failed instanceof Error ? failed.message : String(failed));
          setState((now) => ({ ...now, reading: false }));
        });
    }, pollMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [state, pollMs]);

  async function readNow() {
    setStarting(true);
    setError(null);
    try {
      setState(await startRepoRefresh());
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : String(failed));
    } finally {
      setStarting(false);
    }
  }

  const busy = starting || state.reading;
  const result = state.reading ? null : state.result;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        <CodeText text={t.note} />
      </p>
      <dl className="flex flex-col gap-1.5">
        <Fact label={t.lastRead}>
          {state.last_read_at ? (
            <time dateTime={state.last_read_at}>
              {formatDateTime(state.last_read_at, zone, language)}
            </time>
          ) : (
            <span className="text-muted-foreground">{t.notYet}</span>
          )}
        </Fact>
      </dl>
      {state.repos.length === 0 ? (
        <p className="text-sm text-ink-2">
          <CodeText text={t.none} />
        </p>
      ) : (
        <ul aria-label={t.repos} className="flex flex-col gap-1.5">
          {state.repos.map(({ repo, chunks }) => (
            <li
              key={repo}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-row bg-soft px-3 py-2"
            >
              <code className="min-w-0 font-mono text-sm break-all">{repo}</code>
              <span className="ml-auto text-xs text-muted-foreground">{t.chunks(chunks)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button wrap type="button" size="sm" disabled={busy} onClick={() => void readNow()}>
          {busy && <Loader2 aria-hidden className="animate-spin" />}
          {busy ? t.reading : t.button}
        </Button>
        {result && !result.failed && (
          <p role="status" className="text-sm text-muted-foreground">
            {result.read === result.linked
              ? t.readAll(result.read)
              : t.readSome(result.read, result.linked)}
          </p>
        )}
      </div>
      {result?.failed && <Alert>{t.failed}</Alert>}
      {error && <Alert>{t.requestFailed(error)}</Alert>}
    </div>
  );
}
