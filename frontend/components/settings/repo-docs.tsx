"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { CodeText } from "@/components/code-text";
import { useTimeZone } from "@/components/preferences";
import { Alert } from "@/components/settings/loaded";
import { Fact } from "@/components/settings/secret";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { useLanguage, useT } from "@/lib/i18n";
import { refreshRepoDocs, type RepoDocsRefreshed, type RepoDocsState } from "@/lib/knowledge-api";

/**
 * The docs of the repos services link, as Bleep holds them: when they were last read and the
 * chunks each repo has. "Re-read docs" reads them now, for a person who fixed their docs after an
 * incident and does not want to wait for the daily read.
 */
export function RepoDocs({ initial }: { initial: RepoDocsState }) {
  const t = useT().settings.docs;
  const zone = useTimeZone();
  const language = useLanguage();
  const [state, setState] = useState<RepoDocsState>(initial);
  const [done, setDone] = useState<RepoDocsRefreshed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);

  async function readNow() {
    setReading(true);
    setError(null);
    try {
      const result = await refreshRepoDocs();
      setState(result);
      setDone(result);
    } catch (failed) {
      setDone(null);
      setError(failed instanceof Error ? failed.message : String(failed));
    } finally {
      setReading(false);
    }
  }

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
        <Button wrap type="button" size="sm" disabled={reading} onClick={() => void readNow()}>
          {reading && <Loader2 aria-hidden className="animate-spin" />}
          {reading ? t.reading : t.button}
        </Button>
        {done && (
          <p role="status" className="text-sm text-muted-foreground">
            {done.read === done.linked ? t.readAll(done.read) : t.readSome(done.read, done.linked)}
          </p>
        )}
      </div>
      {error && <Alert>{t.failed(error)}</Alert>}
    </div>
  );
}
